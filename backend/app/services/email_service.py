from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.config import Settings
from app.services.automation_runtime import AutomationRuntime


class EmailConfigurationError(RuntimeError):
    pass


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

        message_id = make_msgid(domain=settings.smtp_from_email.split("@")[-1])
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
        with smtp_class(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout,
        ) as client:
            if not settings.smtp_use_ssl and settings.smtp_use_tls:
                client.starttls()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return message_id
