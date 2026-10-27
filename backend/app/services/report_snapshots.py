"""L1 -> SQLite -> provider, with cross-process singleflight and stale-while-refresh."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import monotonic, sleep, time
from typing import Callable

from app.database.report_snapshots import ReportKey, ReportSnapshot, ReportSnapshotRepository, fingerprint
from app.services.cache_revision import CacheRevision
from app.services.process_lock import FileLock
from app.services.query_cache import QueryCache
from app.services.report_deadline import ensure_time_remaining
from app.services.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations


class SnapshotRefreshBusy(RuntimeError):
    pass


@dataclass
class ReportData:
    payload: object
    meta: dict


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
        source = 'cache'
        def read():
            nonlocal source
            source = 'snapshot'
            return self.repository.get(key)
        record = self._cache.read((*key.values, self.revision.version()), read,
                                  ttl=max(0, min(30, self.settings.report_data_cache_seconds)))
        return deepcopy(record), source

    def _result(self, snapshot: ReportSnapshot, source: str, *, refreshing: bool = False, error: bool = False) -> ReportData:
        return ReportData(deepcopy(snapshot.payload), {
            'line_id': snapshot.key.line_id, 'report_type': snapshot.key.report_type,
            'data_version': snapshot.data_version, 'data_as_of': snapshot.data_as_of.isoformat(),
            'refreshed_at': snapshot.refreshed_at.isoformat(), 'expires_at': snapshot.expires_at.isoformat(),
            'source': source, 'stale': snapshot.expires_at <= datetime.now(timezone.utc),
            'refreshing': refreshing, 'refresh_failed': error,
        })

    def read(self, key: ReportKey, load: Callable[[], dict], *, ttl: int, force: bool = False,
             requested_at: float | None = None, background: bool = False) -> ReportData:
        requested_at = time() if requested_at is None else requested_at
        record, source = self._get(key)
        now = datetime.now(timezone.utc)
        payload = self.task_payload(key)
        refreshing = bool(self.coordination and self.coordination.snapshot_refresh_active(key.line_id, payload))
        if record and record.expires_at > now and (not force or background):
            return self._result(record, source, refreshing=refreshing)
        # Stale statistics are read-only; detail queries and all writes keep their own live validation.
        fallback = record if record and now - record.expires_at <= timedelta(seconds=max(1, self.settings.leadership_report_history_cache_seconds)) else None
        if fallback and not force and not background and self.coordination:
            self.coordination.request_snapshot_refresh(key.line_id, payload)
            refreshing = self.coordination.snapshot_refresh_active(key.line_id, payload)
            return self._result(fallback, source, refreshing=refreshing)
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
            latest = self.repository.get(key)
            now = datetime.now(timezone.utc)
            if latest and latest.expires_at > now and (not force or background or latest.refreshed_at.timestamp() >= requested_at):
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
            self.repository.save(snapshot)
            self.revision.publish()
            return self._result(snapshot, 'database')
        finally:
            lock.release()
