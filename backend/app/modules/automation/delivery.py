from __future__ import annotations

import logging
from datetime import datetime, timedelta
from hashlib import sha256
from time import monotonic

from app.config import Settings
from app.core.operations_logging import event
from app.modules.automation.delivery_repository import DeliveryRepository
from app.modules.automation.email import EmailService, EmailConfigurationError, EmailDeliveryFailure, EmailRecipientsRefused
from app.modules.automation.runtime import AutomationRuntime
from app.services.report_service import archived_report_path


# Persisted operations incident fingerprints include the logger name.
logger = logging.getLogger('app.services.automation_service')


class DeliveryService:
    def __init__(self, settings: Settings, repository: DeliveryRepository, email: EmailService, runtime: AutomationRuntime) -> None:
        self.settings = settings
        self.repository = repository
        self.email_service = email
        self.runtime = runtime

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
        # Any error after SMTP dispatch leaves the delivery outcome uncertain and must prevent a resend.
        except Exception as exc:  # noqa: BLE001
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
