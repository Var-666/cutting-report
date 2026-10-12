from __future__ import annotations

import hashlib
import logging
import threading
from time import monotonic
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol
from uuid import uuid4

from app.config import Settings
from app.database.alarms import AlarmRepository, AlarmLeaseLostError
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.alarm_report_service import AlarmReportService


logger = logging.getLogger(__name__)


class AlarmSource(Protocol):
    def latest_id(self) -> int: ...

    def fetch_new(
        self, after_id: int, through_id: int, limit: int
    ) -> list[dict[str, Any]]: ...

    def fetch_recent(
        self,
        after_id: int,
        through_id: int,
        start_at: str,
        end_at: str,
        limit: int,
    ) -> list[dict[str, Any]]: ...

    def fetch_by_ids(self, source_ids: list[int]) -> list[dict[str, Any]]: ...


class AlarmMonitorService:
    def __init__(
        self,
        settings: Settings,
        repository: AlarmRepository,
        source: AlarmSource,
        analyzer: AlarmAnalysisService,
        report_service: AlarmReportService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.source = source
        self.analyzer = analyzer
        self.report_service = report_service
        self.owner = f"alarm-monitor-{uuid4().hex[:12]}"
        self._run_lock = threading.Lock()

    LEASE_TTL_SECONDS = 600
    LEASE_RENEW_SECONDS = 60

    @staticmethod
    def _skipped(reason: str) -> dict[str, Any]:
        return {"status": "skipped", "sync_status": "skipped", "report_status": "skipped",
                "reason": reason, "report_id": None}

    def run_once(self, now: datetime) -> dict[str, Any]:
        if not self._run_lock.acquire(blocking=False):
            return self._skipped("same_service_running")
        owner = f"{self.owner}-{uuid4().hex[:12]}"
        started = monotonic()
        lease_now = lambda: now + timedelta(seconds=monotonic() - started)
        stop_renewal = threading.Event()
        lost = threading.Event()
        heartbeat = None
        acquired = False
        try:
            acquired = self.repository.try_acquire_lease(owner, lease_now(), ttl_seconds=self.LEASE_TTL_SECONDS)
            if not acquired:
                return self._skipped("another_worker")

            def renew() -> None:
                while not stop_renewal.wait(self.LEASE_RENEW_SECONDS):
                    try:
                        if not self.repository.renew_lease(owner, lease_now(), ttl_seconds=self.LEASE_TTL_SECONDS):
                            lost.set()
                            return
                    except Exception:
                        lost.set()
                        logger.exception("alarm lease renewal failed")
                        return

            heartbeat = threading.Thread(target=renew, name="alarm-lease-renewal", daemon=True)
            heartbeat.start()
            self.repository.recover_interrupted_reports(owner, lease_now())
            return self._run_owned(now, owner, lease_now, lost)
        finally:
            stop_renewal.set()
            if heartbeat is not None and heartbeat.ident is not None:
                heartbeat.join(timeout=5)
                if heartbeat.is_alive():
                    logger.warning("alarm lease renewal still waiting for database during shutdown")
            try:
                if acquired:
                    self.repository.release_lease(owner)
            finally:
                self._run_lock.release()

    def _check_lease(self, owner: str, lease_now: Callable[[], datetime], lost: threading.Event) -> None:
        if lost.is_set() or not self.repository.renew_lease(owner, lease_now(), ttl_seconds=self.LEASE_TTL_SECONDS):
            raise AlarmLeaseLostError("告警租约失效，停止后续分析和渲染")

    def _run_owned(self, now: datetime, owner: str,
                   lease_now: Callable[[], datetime], lost: threading.Event) -> dict[str, Any]:
        self.repository.configure_monitoring_window(
            self.settings.alarm_monitor_start_at, lease_owner=owner, lease_now=lease_now()
        )
        sync_state = self.repository.get_sync_state()
        cursor = int(sync_state["last_alarm_id"])
        batch_size = min(5000, max(1, self.settings.alarm_monitor_batch_size))
        by_id: dict[int, dict[str, Any]] = {}

        # Capture REMOVE_TIME changes for alarms inserted in earlier scans.
        open_ids = self.repository.list_open_source_ids(
            monitor_start_at=self.settings.alarm_monitor_start_at,
            after_id=int(sync_state["open_recheck_after_id"]),
        )
        if not open_ids and sync_state["open_recheck_after_id"]:
            open_ids = self.repository.list_open_source_ids(
                monitor_start_at=self.settings.alarm_monitor_start_at
            )
        open_cursor = open_ids[-1] if len(open_ids) == 2000 else 0
        for record in self.source.fetch_by_ids(open_ids):
            by_id[int(record["alarm_info_id"])] = record

        next_cursor = cursor
        high_water = self.source.latest_id()
        # Each run reads to one fixed high-water mark. When fewer than one
        # batch matches the start-time window, the cursor can safely advance
        # across older excluded ids without rescanning them next time.
        for _ in range(20):
            rows = self.source.fetch_new(next_cursor, high_water, batch_size)
            if not rows:
                next_cursor = high_water
                break
            for record in rows:
                source_id = int(record["alarm_info_id"])
                by_id[source_id] = record
                next_cursor = max(next_cursor, source_id)
            if len(rows) < batch_size:
                next_cursor = high_water
                break

        recent_start = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        monitor_start = self.settings.alarm_monitor_start_at.replace("T", " ")[:19]
        recent_start = max(recent_start, monitor_start)
        recent_end = now.strftime("%Y-%m-%d %H:%M:%S")
        recent_cursor = 0
        for _ in range(20):
            if recent_cursor >= high_water:
                break
            rows = self.source.fetch_recent(
                recent_cursor,
                high_water,
                recent_start,
                recent_end,
                batch_size,
            )
            if not rows:
                break
            previous_cursor = recent_cursor
            for record in rows:
                source_id = int(record["alarm_info_id"])
                by_id[source_id] = record
                recent_cursor = max(recent_cursor, source_id)
            if recent_cursor <= previous_cursor:
                raise RuntimeError("告警近期回查游标未向前推进")
            if len(rows) < batch_size:
                break

        # Rotate one bounded page over the entire configured window, so
        # late rows older than 24 hours are eventually revisited too.
        history_cursor = int(sync_state["history_recheck_after_id"])
        if history_cursor >= high_water:
            history_cursor = 0
        history_rows = self.source.fetch_recent(
            history_cursor, high_water, monitor_start, recent_end, batch_size
        )
        next_history_cursor = history_cursor
        for record in history_rows:
            source_id = int(record["alarm_info_id"])
            by_id[source_id] = record
            next_history_cursor = max(next_history_cursor, source_id)
        if history_rows and next_history_cursor <= history_cursor:
            raise RuntimeError("告警历史回查游标未向前推进")
        if len(history_rows) < batch_size or next_history_cursor >= high_water:
            next_history_cursor = 0

        ingestion = self.repository.ingest(
            by_id.values(),
            now=now,
            cursor=next_cursor,
            debounce_minutes=self.settings.alarm_reopen_debounce_minutes,
            monitor_start_at=self.settings.alarm_monitor_start_at,
            open_recheck_after_id=open_cursor,
            history_recheck_after_id=next_history_cursor,
            expired_before=(now - timedelta(days=self.settings.alarm_retention_days)
                            if self.settings.alarm_retention_days > 0 else None),
            lease_owner=owner, lease_now=lease_now(),
        )
        self._check_lease(owner, lease_now, lost)
        incidents = self.repository.list_unreported_incidents(
            limit=100,
            monitor_start_at=self.settings.alarm_monitor_start_at,
        )
        if not incidents:
            return {
                "status": "completed",
                "sync_status": "completed",
                "report_status": "idle",
                "report_id": None,
                "records": ingestion["records"],
                "new_incidents": ingestion["created_incidents"],
            }

        incident_ids = sorted(item["incident_id"] for item in incidents)
        emission_key = hashlib.sha256("\n".join(incident_ids).encode("utf-8")).hexdigest()
        proposed_id = f"alarm_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
        machine_no = ", ".join(sorted({item["machine_no"] for item in incidents}))
        report_id = self.repository.reserve_report(
            proposed_report_id=proposed_id,
            emission_key=emission_key,
            machine_no=machine_no,
            incident_ids=incident_ids,
            now=now.isoformat(timespec="seconds"),
            lease_owner=owner, lease_now=lease_now(),
        )
        artifact = None
        try:
            self._check_lease(owner, lease_now, lost)
            analysis = self.analyzer.analyze(incidents)
            self._check_lease(owner, lease_now, lost)
            artifact = self.report_service.render(
                report_id=report_id,
                machine_no=machine_no,
                analysis=analysis,
                generated_at=now,
                execution_id=owner,
            )
            self.repository.complete_report(
                report_id,
                analysis=analysis,
                model_status="model",
                completed_at=now.isoformat(timespec="seconds"),
                lease_owner=owner, lease_now=lease_now(),
                **artifact,
            )
        except Exception as exc:
            if artifact is not None:
                self.report_service.discard(artifact)
            if isinstance(exc, AlarmLeaseLostError):
                logger.warning("discarded alarm report after losing execution ownership", extra={"report_id": report_id})
                raise
            self.repository.fail_report(report_id, str(exc), lease_owner=owner, lease_now=lease_now())
            logger.exception(
                "alarm report generation failed",
                extra={"report_id": report_id, "incident_count": len(incidents)},
            )
            return {
                "status": "completed",
                "sync_status": "completed",
                "report_status": "failed",
                "report_id": report_id,
                "report_error": str(exc),
                "records": ingestion["records"],
                "new_incidents": ingestion["created_incidents"],
                "reported_incidents": 0,
                "model_status": "failed",
            }

        logger.info(
            "alarm report generated",
            extra={"report_id": report_id, "incident_count": len(incidents)},
        )
        return {
            "status": "completed",
            "sync_status": "completed",
            "report_status": "completed",
            "report_id": report_id,
            "records": ingestion["records"],
            "new_incidents": ingestion["created_incidents"],
            "reported_incidents": len(incidents),
            "model_status": "model",
        }
