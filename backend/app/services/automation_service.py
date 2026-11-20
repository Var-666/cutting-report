from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from time import monotonic
from uuid import uuid4

from app.core.operations_logging import event
from app.core.sql_capacity import sql_identity
from app.config import Settings
from app.database.automation import AutomationRepository
from app.database.metadata import MetadataRepository
from app.database.unit_of_work import UnitOfWork
from app.models.domain import ReportFilter
from app.services.email_service import (
    EmailService, EmailConfigurationError, EmailDeliveryFailure,
    EmailRecipientsRefused,
)
from app.services.automation_runtime import AutomationRuntime
from app.services.report_service import archived_report_path

if TYPE_CHECKING:
    from app.services.database_runtime import DatabaseRuntime
    from app.services.device_runtime import DeviceRuntime
    from app.services.production_lines import ProductionLineRuntime


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
        runtime: AutomationRuntime | None = None,
        *,
        line_services: dict[str, ProductionLineRuntime] | None = None,
        database_runtime: DatabaseRuntime | None = None,
    ) -> None:
        if repository.database is not metadata.database:
            raise ValueError("自动化仓储与元数据仓储必须使用同一个 PostgreSQL 连接池")
        self.settings = settings
        self.repository = repository
        self.metadata = metadata
        self.email_service = email_service
        self.runtime = runtime or AutomationRuntime(settings)
        self.database = metadata.database
        self.line_services = line_services if line_services is not None else {}
        self.database_runtime = database_runtime

    def enrich_schedule(self, data: dict[str, Any], now: datetime) -> dict[str, Any]:
        value = dict(data)
        value["next_run_at"] = next_run_at(value, now).isoformat(timespec="seconds")
        return value

    def process_due_schedules(self, now: datetime) -> int:
        runtime_settings, _ = self.runtime.snapshot()
        if not runtime_settings.automation_enabled:
            return 0
        count = 0
        for schedule in self.repository.due_schedules(now.isoformat(timespec="seconds")):
            try:
                scheduled_for = datetime.fromisoformat(schedule["next_run_at"])
                following = next_run_at(schedule, scheduled_for)
                run = self.repository.reserve_run(
                    schedule,
                    scheduled_for=schedule["next_run_at"],
                    report_date=report_date_for(schedule, scheduled_for).isoformat(),
                    trigger_type="scheduled",
                    now=now.isoformat(timespec="seconds"),
                    next_run_at=following.isoformat(timespec="seconds"),
                )
                if run and self._create_report_job(run, now):
                    count += 1
            except Exception as exc:
                event(logger, logging.ERROR, '自动计划入队失败', error_code='AUTOMATION_SCHEDULE_FAILED',
                      resource_id=schedule['schedule_id'], line_id=schedule.get('device_id'), stage='scheduling',
                      error_type=type(exc).__name__)
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
        # Worker reconciliation prepares the report; HTTP only reserves durable work.
        return self.repository.get_run(run["run_id"])

    def run_progress(self, run_id: str) -> dict | None:
        run = self.repository.get_run(run_id)
        if run is None or run['status'] == 'cancelled' or not run["report_id"]:
            return run
        delivery = self.repository.delivery_for_report(run["report_id"])
        if delivery is not None:
            return {**run, "status": "email_pending" if delivery["status"] == "pending" else delivery["status"],
                    "last_error": delivery["last_error"]}
        job = self.metadata.get_job(run['report_id'])
        if job and job['status'] == 'pending' and job.get('ai_status') == 'waiting':
            return {**run, 'status': 'analysis_wait', 'last_error': None}
        return run

    def cancel_run(self, run_id: str, now: datetime) -> dict | None:
        return self.repository.cancel_manual_run(run_id, now=now.isoformat(timespec='seconds'))

    def _create_report_job(self, run: dict, now: datetime) -> bool:
        try:
            lines = self.database_runtime.snapshot().lines if self.database_runtime else self.line_services
            line = lines.get(run["device_id"])
            if line is None or not line.config.enabled or line.pipeline is None:
                raise ValueError("计划产线未启用或不支持自动化报表")
            if run["report_type"] == "workshop":
                self._ensure_workshop_report(run, now, line.cutting())
            report_id = f"rpt_{now:%Y%m%d_%H%M%S}_{uuid4().hex[:6]}"
            with UnitOfWork(self.database, self.settings.timezone, device_id=run["device_id"], scope_machine_no=line.settings.report_machine_no) as unit:
                current = unit.automation.get_run(run["run_id"], for_update=True)
                if current is None or current["report_id"] or current["status"] != "queued":
                    return False
                unit.metadata.create_job(
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
                unit.automation.attach_report(
                    run["run_id"], report_id, now=now.isoformat(timespec="seconds")
                )
            return True
        except Exception as exc:
            logger.exception("scheduled report enqueue failed")
            with UnitOfWork(self.database, self.settings.timezone) as unit:
                current = unit.automation.get_run(run["run_id"], for_update=True)
                if current and not current["report_id"] and current["status"] == "queued":
                    unit.automation.mark_run(
                        run["run_id"], "failed", now=now.isoformat(timespec="seconds"), error=str(exc)
                    )
            return False

    def reconcile_runs(self, now: datetime) -> int:
        """Resume only persisted gaps; never recreate jobs or redeliver mail."""
        recovered = 0
        for item in self.repository.incomplete_runs():
            try:
                if item["report_id"] is None:
                    run = self.repository.get_run(item["run_id"])
                    if run and self._create_report_job(run, now):
                        recovered += 1
                elif item["report_status"] == "completed":
                    self.report_completed(item["report_id"], now)
                    recovered += 1
                elif item["report_status"] == "failed":
                    self.report_failed(item["report_id"], item["error_message"] or "报表生成失败", now)
                    recovered += 1
            except Exception as exc:
                event(logger, logging.ERROR, '自动计划恢复失败', error_code='AUTOMATION_RECOVERY_FAILED',
                      run_id=item['run_id'], report_id=item['report_id'], stage='recovery', error_type=type(exc).__name__)
        return recovered

    def _ensure_workshop_report(self, run: dict, now: datetime, device: DeviceRuntime) -> None:
        report_date = run["report_date"]
        cfg = device.settings
        query = device.query_service
        workshop = device.workshop
        with sql_identity(device.settings.device_id):
            bundle = query.fetch_workshop_data(
                ReportFilter(report_date=date.fromisoformat(report_date))
            )
        user = {
            "user_id": run["created_by_id"],
            "display_name": run["created_by_name"],
        }
        workshop.sync_daily_report(
            report_date=report_date,
            source_mode=cfg.effective_data_source_mode,
            records=bundle.workshop_records,
            user=user,
            now=now.isoformat(timespec="seconds"),
            scope_machine_no=cfg.report_machine_no,
        )

    def report_completed(self, report_id: str, now: datetime) -> None:
        run = self.repository.delivery_for_report(report_id)
        if run is not None:
            return
        values = self.repository.get_run_for_report(report_id)
        if values is None:
            return
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
        run = self.repository.get_run_for_report(report_id)
        if run:
            self.repository.mark_run(
                run["run_id"], "failed", now=now.isoformat(timespec="seconds"), error=error
            )

    def process_next_email(self, now: datetime) -> bool:
        started = monotonic()
        delivery = self.repository.claim_delivery(now.isoformat(timespec="seconds"))
        if delivery is None:
            return False
        runtime_settings, _ = self.runtime.snapshot()
        send_started = False
        stage = 'preparing'
        try:
            if delivery.get('ai_status') != 'success':
                self.repository.rebuild_unanalyzed_delivery(delivery, now=now.isoformat(timespec='seconds'))
                return True
            stage = 'archive'
            path = archived_report_path(self.settings.reports_dir, delivery)
            content = path.read_bytes()
            if sha256(content).hexdigest() != delivery["sha256"]:
                raise OSError("归档 HTML 校验失败")
            attachment = content if delivery.get("attach_report") else None
            send_started = True
            stage = 'smtp'
            message_id = self.email_service.send(
                delivery, attachment, settings_snapshot=runtime_settings, report_path=path
            )
        except Exception as exc:
            elapsed = max(0, monotonic() - started)
            now += timedelta(seconds=elapsed)
            delay = min(
                runtime_settings.email_retry_base_seconds
                * (2 ** max(0, int(delivery["attempt_count"]) - 1)),
                3600,
            )
            retry_at = now + timedelta(seconds=delay)
            unknown = send_started and not isinstance(exc, (EmailRecipientsRefused, EmailDeliveryFailure, EmailConfigurationError))
            event(logger, logging.WARNING if unknown else logging.ERROR,
                  '邮件发送结果待核对' if unknown else '邮件发送尝试失败',
                  error_code='SMTP_OUTCOME_UNKNOWN' if unknown else 'SMTP_ATTEMPT_FAILED',
                  resource_id=delivery['delivery_id'], report_id=delivery.get('report_id'), related_kind='email',
                  line_id=delivery.get('device_id'), run_id=delivery.get('run_id'), stage=stage,
                  attempt=delivery['attempt_count'], error_type=type(exc).__name__, duration_ms=elapsed * 1000)
            if isinstance(exc, EmailRecipientsRefused):
                self.repository.mark_delivery_refused(
                    delivery, exc.recipients, message_id=exc.message_id,
                    next_attempt_at=retry_at.isoformat(timespec="seconds"),
                    now=now.isoformat(timespec="seconds"),
                )
            elif send_started and not isinstance(exc, (EmailDeliveryFailure, EmailConfigurationError)):
                self.repository.mark_delivery_unknown(delivery, now=now.isoformat(timespec="seconds"))
            else:
                self.repository.mark_delivery_failed(
                    delivery, error=str(exc) if isinstance(exc, (EmailDeliveryFailure, EmailConfigurationError))
                    else ("归档 HTML 缺失、损坏或暂时无法读取，尚未提交 SMTP" if stage == 'archive'
                          else "邮件准备暂时失败，尚未提交 SMTP"),
                    next_attempt_at=retry_at.isoformat(timespec="seconds"),
                    now=now.isoformat(timespec="seconds"),
                )
            return True
        # SMTP success followed by a database failure is an unknown durable
        # outcome. Leave 'sending' for restart reconciliation; never retry it.
        now += timedelta(seconds=max(0, monotonic() - started))
        try:
            complete = self.repository.mark_delivery_sent(
                delivery, message_id=message_id, now=now.isoformat(timespec="seconds")
            )
        except Exception as exc:
            event(logger, logging.ERROR, 'SMTP 已接收，状态提交待核对', error_code='SMTP_PERSISTENCE_UNCERTAIN',
                  resource_id=delivery['delivery_id'], report_id=delivery.get('report_id'), related_kind='email',
                  line_id=delivery.get('device_id'), run_id=delivery.get('run_id'), stage='persisting',
                  attempt=delivery['attempt_count'], error_type=type(exc).__name__, duration_ms=(monotonic() - started) * 1000)
            raise
        event(logger, logging.INFO, '邮件已由 SMTP 接收并确认入库' if complete else '部分收件人被 SMTP 永久拒收',
              error_code='SMTP_ACCEPTED' if complete else 'SMTP_PARTIAL_REFUSAL',
              resource_id=delivery['delivery_id'], report_id=delivery.get('report_id'), related_kind='email',
              line_id=delivery.get('device_id'), run_id=delivery.get('run_id'), stage='sent' if complete else 'failed',
              attempt=delivery['attempt_count'], duration_ms=(monotonic() - started) * 1000)
        return True
