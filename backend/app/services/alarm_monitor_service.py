from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from app.config import Settings
from app.database.alarms import AlarmRepository
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.alarm_report_service import AlarmReportService


logger = logging.getLogger(__name__)


class AlarmSource(Protocol):
    def latest_id(self) -> int: ...

    def fetch_open(self, limit: int) -> list[dict[str, Any]]: ...

    def fetch_new(self, after_id: int, limit: int) -> list[dict[str, Any]]: ...

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

    def run_once(self, now: datetime) -> dict[str, Any]:
        if not self.repository.try_acquire_lease(self.owner, now):
            return {"status": "skipped", "reason": "another_worker", "report_id": None}
        try:
            sync_state = self.repository.get_sync_state()
            cursor = int(sync_state["last_alarm_id"])
            batch_size = min(5000, max(1, self.settings.alarm_monitor_batch_size))
            by_id: dict[int, dict[str, Any]] = {}

            # Capture REMOVE_TIME changes for alarms inserted in earlier scans.
            open_ids = self.repository.list_open_source_ids()
            for record in self.source.fetch_by_ids(open_ids):
                by_id[int(record["alarm_info_id"])] = record

            if sync_state["last_success_at"] is None:
                # First deployment must not emit hundreds of PDFs for historical
                # closed alarms. Bootstrap the cursor at the current high-water
                # mark, while still reporting alarms that are open right now.
                next_cursor = self.source.latest_id()
                for record in self.source.fetch_open(min(5000, batch_size * 20)):
                    by_id[int(record["alarm_info_id"])] = record
            else:
                next_cursor = cursor
                # Drain a bounded backlog in one execution. A remaining backlog is
                # picked up on the next five-minute run without losing the cursor.
                for _ in range(20):
                    rows = self.source.fetch_new(next_cursor, batch_size)
                    if not rows:
                        break
                    for record in rows:
                        source_id = int(record["alarm_info_id"])
                        by_id[source_id] = record
                        next_cursor = max(next_cursor, source_id)
                    if len(rows) < batch_size:
                        break

            ingestion = self.repository.ingest(
                by_id.values(),
                now=now,
                cursor=next_cursor,
                debounce_minutes=self.settings.alarm_reopen_debounce_minutes,
            )
            incidents = self.repository.list_unreported_incidents(limit=100)
            if not incidents:
                return {
                    "status": "completed",
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
            )
            try:
                analysis, model_status = self.analyzer.analyze(incidents)
                artifact = self.report_service.render(
                    report_id=report_id,
                    machine_no=machine_no,
                    analysis=analysis,
                    generated_at=now,
                )
                self.repository.complete_report(
                    report_id,
                    analysis=analysis,
                    model_status=model_status,
                    completed_at=now.isoformat(timespec="seconds"),
                    **artifact,
                )
            except Exception as exc:
                self.repository.fail_report(report_id, str(exc))
                raise

            logger.info(
                "alarm report generated",
                extra={"report_id": report_id, "incident_count": len(incidents)},
            )
            return {
                "status": "completed",
                "report_id": report_id,
                "records": ingestion["records"],
                "new_incidents": ingestion["created_incidents"],
                "reported_incidents": len(incidents),
                "model_status": model_status,
            }
        finally:
            self.repository.release_lease(self.owner)
