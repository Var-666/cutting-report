from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from time import monotonic

from app.config import Settings
from app.services.automation_runtime import AutomationRuntime


class EmailConfigurationError(RuntimeError):
    pass


class EmailDeliveryFailure(RuntimeError):
    """The SMTP server has not accepted this attempt; retry is safe."""


class EmailDeliveryUnknown(RuntimeError):
    """Acceptance could not be determined; automatic retry is unsafe."""


class EmailRecipientsRefused(EmailDeliveryFailure):
    def __init__(self, recipients: dict, message_id: str) -> None:
        super().__init__("SMTP 拒收部分或全部收件人")
        self.recipients = recipients
        self.message_id = message_id


class EmailService:
    def __init__(
        self, settings: Settings, runtime: AutomationRuntime | None = None
    ) -> None:
        self.settings = settings
        self.runtime = runtime or AutomationRuntime(settings)

    @staticmethod
    def _configured(settings: Settings) -> bool:
        return bool(
            settings.smtp_enabled
            and settings.smtp_host
            and settings.smtp_from_email
        )

    @property
    def configured(self) -> bool:
        settings, _ = self.runtime.snapshot()
        return self._configured(settings)

    def status(self, *, settings_snapshot: Settings | None = None) -> dict:
        settings = settings_snapshot or self.runtime.snapshot()[0]
        return {
            "enabled": settings.smtp_enabled,
            "configured": self._configured(settings),
            "host": settings.smtp_host or None,
            "port": settings.smtp_port,
            "from_email": settings.smtp_from_email or None,
            "use_tls": settings.smtp_use_tls,
            "use_ssl": settings.smtp_use_ssl,
        }

    def send(
        self,
        delivery: dict,
        attachment: bytes | None,
        *,
        settings_snapshot: Settings | None = None,
    ) -> str:
        settings = settings_snapshot or self.runtime.snapshot()[0]
        if not self._configured(settings):
            raise EmailConfigurationError("SMTP 尚未配置或未启用")

        message_id = delivery.get("message_id") or make_msgid(domain=settings.smtp_from_email.split("@")[-1])
        message = EmailMessage()
        message["Message-ID"] = message_id
        message["From"] = formataddr(
            (settings.smtp_from_name, settings.smtp_from_email)
        )
        message["To"] = ", ".join(delivery["recipients"])
        message["Subject"] = delivery["subject"]
        message.set_content(
            "\n".join(
                [
                    f"报表：{delivery['schedule_name']}",
                    f"报表期间：{delivery['report_date']}",
                    f"生成时间：{delivery.get('generated_at') or '--'}",
                    "",
                    "本邮件由切割生产智能产报系统自动发送。",
                ]
            )
        )
        if attachment is not None:
            message.add_attachment(
                attachment,
                maintype="text",
                subtype="html",
                filename=delivery["filename"],
            )

        smtp_class = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
        submitted = False
        refused = None
        deadline = monotonic() + settings.smtp_timeout

        def prepare_socket(client, *, required: bool) -> None:
            remaining = deadline - monotonic()
            socket = getattr(client, "sock", None)
            if socket is not None and hasattr(socket, "settimeout"):
                socket.settimeout(max(0.001, min(settings.smtp_timeout, remaining)))
            if required and remaining <= 0:
                raise TimeoutError("SMTP 单次发送超过总时限")

        try:
            with smtp_class(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout,
            ) as client:
                prepare_socket(client, required=True)
                if not settings.smtp_use_ssl and settings.smtp_use_tls:
                    client.starttls()
                    prepare_socket(client, required=True)
                if settings.smtp_username:
                    client.login(settings.smtp_username, settings.smtp_password)
                    prepare_socket(client, required=True)
                submitted = True
                try:
                    refused = client.send_message(message) or {}
                except smtplib.SMTPRecipientsRefused as exc:
                    refused = exc.recipients
                finally:
                    # Bound QUIT as well, without changing an already-known
                    # accepted/refused result into an artificial failure.
                    prepare_socket(client, required=False)
        except Exception as exc:
            # A QUIT failure cannot undo an already received DATA reply.
            if refused is None:
                if not submitted or (
                    isinstance(exc, smtplib.SMTPResponseException)
                    and 400 <= exc.smtp_code < 600
                ) or isinstance(exc, smtplib.SMTPNotSupportedError):
                    raise EmailDeliveryFailure("SMTP 明确失败，尚未接受邮件") from exc
                raise EmailDeliveryUnknown("SMTP 接受结果未知，请按 Message-ID 核对服务器记录") from exc
        if refused:
            raise EmailRecipientsRefused(refused, message_id)
        return message_id
