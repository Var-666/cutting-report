from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.database.automation import AutomationRepository
from app.database.metadata import MetadataRepository
from app.database.workshop import DailyReportExistsError, WorkshopRepository
from app.models.domain import ReportFilter
from app.services.email_service import EmailService
from app.services.query_service import QueryService


logger = logging.getLogger(__name__)
REPORT_NAMES = {
    "leadership": "领导报表",
    "workshop": "车间报表",
    "equipment": "设备报表",
}


def next_run_at(schedule: dict, after: datetime) -> datetime:
    hour, minute = (int(part) for part in schedule["run_time"].split(":"))
    target_time = time(hour, minute)
    frequency = schedule["frequency"]
    if frequency == "daily":
        candidate = datetime.combine(after.date(), target_time, tzinfo=after.tzinfo)
        return candidate if candidate > after else candidate + timedelta(days=1)
    if frequency == "weekly":
        target_weekday = int(schedule["weekday"])
        days = (target_weekday - after.isoweekday()) % 7
        candidate = datetime.combine(
            after.date() + timedelta(days=days), target_time, tzinfo=after.tzinfo
        )
        return candidate if candidate > after else candidate + timedelta(days=7)

    day = min(int(schedule["month_day"]), 28)
    candidate = datetime.combine(
        after.date().replace(day=day), target_time, tzinfo=after.tzinfo
    )
    if candidate > after:
        return candidate
    year = after.year + (1 if after.month == 12 else 0)
    month = 1 if after.month == 12 else after.month + 1
    return datetime.combine(date(year, month, day), target_time, tzinfo=after.tzinfo)


def report_date_for(schedule: dict, scheduled_for: datetime) -> date:
    period = schedule["report_period"]
    if period == "current_day":
        return scheduled_for.date()
    if period == "previous_day":
        return scheduled_for.date() - timedelta(days=1)
    current_month = scheduled_for.date().replace(day=1)
    if period == "current_month":
        return current_month
    previous_last = current_month - timedelta(days=1)
    return previous_last.replace(day=1)


class AutomationService:
    def __init__(
        self,
        settings: Settings,
        repository: AutomationRepository,
        metadata: MetadataRepository,
        email_service: EmailService,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.metadata = metadata
        self.email_service = email_service
        self.query_service = QueryService(settings)
        self.workshop = WorkshopRepository(settings.metadata_db_path)

    def enrich_schedule(self, data: dict[str, Any], now: datetime) -> dict[str, Any]:
        value = dict(data)
        value["next_run_at"] = next_run_at(value, now).isoformat(timespec="seconds")
        return value

    def process_due_schedules(self, now: datetime) -> int:
        if not self.settings.automation_enabled:
            return 0
        count = 0
        for schedule in self.repository.due_schedules(now.isoformat(timespec="seconds")):
            following = next_run_at(schedule, now)
            run = self.repository.reserve_run(
                schedule,
                scheduled_for=schedule["next_run_at"],
                report_date=report_date_for(
                    schedule, datetime.fromisoformat(schedule["next_run_at"])
                ).isoformat(),
                trigger_type="scheduled",
                now=now.isoformat(timespec="seconds"),
                next_run_at=following.isoformat(timespec="seconds"),
            )
            if run and self._create_report_job(run, now):
                count += 1
        return count

    def run_now(self, schedule_id: str, now: datetime) -> dict | None:
        schedule = self.repository.get_schedule(schedule_id)
        if schedule is None:
            return None
        run = self.repository.reserve_run(
            schedule,
            scheduled_for=now.isoformat(timespec="microseconds"),
            report_date=report_date_for(schedule, now).isoformat(),
            trigger_type="manual",
            now=now.isoformat(timespec="seconds"),
        )
        if run is None:
            return None
        self._create_report_job(run, now)
        return self.repository.get_run(run["run_id"])

    def _create_report_job(self, run: dict, now: datetime) -> bool:
        try:
            if run["report_type"] == "workshop":
                self._ensure_workshop_report(run, now)
            report_id = f"rpt_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
            self.metadata.create_job(
                report_id=report_id,
                report_date=run["report_date"],
                project_code=None,
                ship_no=None,
                request_id=f"automation:{run['run_id']}",
                created_at=now.isoformat(timespec="seconds"),
                report_type=run["report_type"],
                parameters={
                    "chart_type": run["chart_type"],
                    "automation_run_id": run["run_id"],
                },
            )
            self.repository.attach_report(
                run["run_id"], report_id, now=now.isoformat(timespec="seconds")
            )
            return True
        except Exception as exc:
            logger.exception("scheduled report enqueue failed")
            self.repository.mark_run(
                run["run_id"], "failed", now=now.isoformat(timespec="seconds"), error=str(exc)
            )
            return False

    def _ensure_workshop_report(self, run: dict, now: datetime) -> None:
        report_date = run["report_date"]
        if self.workshop.get_daily_report(report_date) is not None:
            return
        bundle = self.query_service.fetch_report_data(
            ReportFilter(report_date=date.fromisoformat(report_date))
        )
        user = {
            "user_id": run["created_by_id"],
            "display_name": run["created_by_name"],
        }
        try:
            self.workshop.create_daily_report(
                report_date=report_date,
                source_mode=self.settings.effective_data_source_mode,
                records=bundle.workshop_records,
                user=user,
                now=now.isoformat(timespec="seconds"),
            )
        except DailyReportExistsError:
            pass

    def report_completed(self, report_id: str, now: datetime) -> None:
        run = self.repository.delivery_for_report(report_id)
        if run is not None:
            return
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scheduled_report_runs WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return
        values = dict(row)
        subject = values["subject_template"]
        replacements = {
            "{report_name}": values["schedule_name"],
            "{report_type}": REPORT_NAMES.get(values["report_type"], values["report_type"]),
            "{report_date}": values["report_date"],
        }
        for key, replacement in replacements.items():
            subject = subject.replace(key, replacement)
        self.repository.enqueue_delivery(
            report_id, subject=subject[:200], now=now.isoformat(timespec="seconds")
        )

    def report_failed(self, report_id: str, error: str, now: datetime) -> None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM scheduled_report_runs WHERE report_id = ?", (report_id,)
            ).fetchone()
        if row:
            self.repository.mark_run(
                row["run_id"], "failed", now=now.isoformat(timespec="seconds"), error=error
            )

    def process_next_email(self, now: datetime) -> bool:
        delivery = self.repository.claim_delivery(now.isoformat(timespec="seconds"))
        if delivery is None:
            return False
        try:
            attachment = None
            if delivery.get("attach_report"):
                root = self.settings.reports_dir.resolve()
                path = (root / delivery["relative_path"]).resolve()
                if not path.is_relative_to(root) or not path.is_file():
                    raise FileNotFoundError("报表附件不存在")
                attachment = path.read_bytes()
            message_id = self.email_service.send(delivery, attachment)
            self.repository.mark_delivery_sent(
                delivery, message_id=message_id, now=now.isoformat(timespec="seconds")
            )
        except Exception as exc:
            delay = min(
                self.settings.email_retry_base_seconds
                * (2 ** max(0, int(delivery["attempt_count"]) - 1)),
                3600,
            )
            retry_at = now + timedelta(seconds=delay)
            logger.warning(
                "email delivery failed delivery_id=%s attempt=%s error_type=%s",
                delivery["delivery_id"], delivery["attempt_count"], type(exc).__name__,
            )
            self.repository.mark_delivery_failed(
                delivery,
                error=str(exc),
                next_attempt_at=retry_at.isoformat(timespec="seconds"),
                now=now.isoformat(timespec="seconds"),
            )
        return True
