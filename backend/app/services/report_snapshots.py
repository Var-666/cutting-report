"""L1 -> PostgreSQL -> provider, with cross-process singleflight and stale-while-refresh."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
from time import monotonic, sleep, time
from typing import Callable
from zoneinfo import ZoneInfo

from app.database.report_snapshots import ReportKey, ReportSnapshot, ReportSnapshotRepository, fingerprint
from app.services.cache_revision import CacheRevision
from app.core.process_lock import FileLock
from app.services.query_cache import QueryCache
from app.services.report_cache_policy import report_ttl
from app.core.operations_logging import data_quality
from app.core.report_deadline import ensure_time_remaining
from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations


logger = logging.getLogger(__name__)


class SnapshotRefreshBusy(RuntimeError):
    pass


class SnapshotRefreshRejected(RuntimeError):
    pass


class SnapshotRefreshFailed(RuntimeError):
    pass


@dataclass
class ReportData:
    payload: object
    meta: dict


def observe_report_snapshot(*, line_id, source_id, report_type, start, end, scope, data_as_of, stale, failed=False):
    if failed:
        state = 'refresh_failed'
        level = logging.ERROR if stale else logging.WARNING
        message = '报表快照已过期且刷新未成功' if stale else '报表刷新未成功，仍在使用有效快照'
    elif stale:
        state, level, message = 'stale', logging.WARNING, '报表正在使用已过期的数据快照'
    else:
        state, level, message = 'verified', logging.INFO, '同一报表范围已取得有效快照'
    data_quality(logger, 'REPORT_SNAPSHOT_DATA_QUALITY', message, scope=scope, level=level, state=state,
                 line_id=line_id, source_id=source_id, stage=report_type,
                 period_start=start, period_end=end, data_as_of=data_as_of)


class ReportSnapshotService:
    def __init__(self, settings, repository: ReportSnapshotRepository, coordination=None,
                 *, unavailable_errors: tuple[type[Exception], ...] = (ConnectionError,)) -> None:
        self.settings = settings
        self.repository = repository
        self.coordination = coordination
        self.unavailable_errors = unavailable_errors
        self.revision = CacheRevision(settings, 'report-snapshots')
        self._cache = QueryCache[ReportSnapshot | None](limit=16)
        identity = sha256(settings.device_id.encode()).hexdigest()[:24]
        self.lock_path = settings.data_dir / '.coordination' / f'report-snapshot-{identity}.lock'

    @staticmethod
    def task_payload(key: ReportKey) -> dict:
        return {'report_type': key.report_type,
                'query': {'start': key.period_start, 'end': key.period_end, **key.filters},
                'source_fingerprint': key.source_fingerprint}

    def _get(self, key: ReportKey) -> tuple[ReportSnapshot | None, str]:
        started = monotonic()
        source = 'cache'
        def read():
            nonlocal source
            source = 'snapshot'
            return self.repository.get(key)
        record = self._cache.read((*key.values, self.revision.version()), read,
                                  ttl=max(0, min(30, self.settings.report_data_cache_seconds)))
        logger.info('report snapshot lookup line=%s report_type=%s start=%s end=%s source=%s hit=%s elapsed_ms=%d',
                    key.line_id, key.report_type, key.period_start, key.period_end, source,
                    record is not None, round((monotonic() - started) * 1000))
        # The record stays read-only; _result copies the payload once at the return boundary.
        return record, source

    def _result(self, snapshot: ReportSnapshot, source: str, *, refreshing: bool = False, error: bool = False) -> ReportData:
        stale = snapshot.expires_at <= datetime.now(timezone.utc)
        key = snapshot.key
        if stale or error or source == 'database':
            observe_report_snapshot(line_id=key.line_id, source_id=key.source_fingerprint, report_type=key.report_type,
                                    start=key.period_start, end=key.period_end, scope=key.values,
                                    data_as_of=snapshot.data_as_of.isoformat(), stale=stale, failed=error)
        # Storage checksum; providers retain their own public report/AI version.
        return ReportData(deepcopy(snapshot.payload), {
            'line_id': snapshot.key.line_id, 'report_type': snapshot.key.report_type,
            'data_version': snapshot.data_version, 'data_as_of': snapshot.data_as_of.isoformat(),
            'refreshed_at': snapshot.refreshed_at.isoformat(), 'expires_at': snapshot.expires_at.isoformat(),
            'source': source, 'stale': stale,
            'refreshing': refreshing, 'refresh_failed': error,
        })

    def read(self, key: ReportKey, load: Callable[[], dict], *, ttl: int | None = None, force: bool = False,
             requested_at: float | None = None, background: bool = False, background_only: bool = False,
             expire_at_midnight: bool = False) -> ReportData:
        unified_policy = ttl is None
        ttl = report_ttl(self.settings, key.period_end or None) if unified_policy else ttl
        def effective(record):
            if record is not None and unified_policy and record.expires_at >= record.refreshed_at:
                record = replace(record, expires_at=record.refreshed_at + timedelta(seconds=max(1, ttl)))
            if record is not None and expire_at_midnight:
                local = record.refreshed_at.astimezone(ZoneInfo(self.settings.timezone))
                if key.period_end >= local.date().isoformat():
                    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
                    record = replace(record, expires_at=min(record.expires_at, midnight))
            return record
        requested_at = time() if requested_at is None else requested_at
        record, source = self._get(key)
        record = effective(record)
        now = datetime.now(timezone.utc)
        payload = self.task_payload(key)
        refreshing, failed = self.coordination.snapshot_refresh_status(key.line_id, payload) if self.coordination else (False, False)
        if background_only and not background and self.coordination:
            if failed and not refreshing and not force:
                if record:
                    return self._result(record, source, refreshing=refreshing, error=True)
                raise SnapshotRefreshFailed('统计刷新失败，请手动重试')
            if (not refreshing or force and failed) and (force or record is None or record.expires_at <= now):
                self.coordination.request_snapshot_refresh(key.line_id, payload, **({'retry_failed': True} if force else {}))
                refreshing, failed = self.coordination.snapshot_refresh_status(key.line_id, payload)
            if record:
                return self._result(record, source, refreshing=refreshing, error=failed or (force or record.expires_at <= now) and not refreshing)
            if not refreshing:
                raise SnapshotRefreshRejected('后台刷新队列已满，请稍后重试')
            raise SnapshotRefreshBusy('报表数据正在后台准备，完成后自动显示')
        if record and record.expires_at > now and (not force or background and (not background_only or record.refreshed_at.timestamp() >= requested_at)):
            return self._result(record, source, refreshing=refreshing, error=failed)
        # Stale statistics are read-only; detail queries and all writes keep their own live validation.
        fallback = record if record and now - record.expires_at <= timedelta(seconds=max(1, self.settings.leadership_report_history_cache_seconds)) else None
        if fallback and not force and not background and self.coordination:
            self.coordination.request_snapshot_refresh(key.line_id, payload)
            refreshing, failed = self.coordination.snapshot_refresh_status(key.line_id, payload)
            return self._result(fallback, source, refreshing=refreshing, error=failed)
        lock = FileLock(self.lock_path)
        started = monotonic()
        while not lock.acquire(blocking=False):
            ensure_time_remaining()
            if sql_request_cancellations.is_cancelled(current_request_id.get()):
                raise SqlRequestCancelled('报表查询已取消')
            if fallback and not background:
                return self._result(fallback, source, refreshing=True)
            if monotonic() - started >= 5:
                raise SnapshotRefreshBusy('统计正在生成，请稍后重试')
            sleep(0.05)
        try:
            # Re-read after the OS lock: Web and Worker share the same singleflight.
            latest = effective(self.repository.get(key))
            now = datetime.now(timezone.utc)
            if latest and latest.expires_at > now and (not force or background and not background_only or latest.refreshed_at.timestamp() >= requested_at):
                return self._result(latest, 'snapshot')
            data_as_of = now
            try:
                result = load()
            except self.unavailable_errors:
                if fallback and not background:
                    return self._result(fallback, source, error=True)
                raise
            refreshed_at = datetime.now(timezone.utc)
            snapshot = ReportSnapshot(key, result, fingerprint(result), data_as_of, refreshed_at,
                                      refreshed_at + timedelta(seconds=max(1, ttl)))
            snapshot = effective(snapshot)
            self.repository.save(snapshot)
            self.revision.publish()
            return self._result(snapshot, 'database')
        finally:
            lock.release()
