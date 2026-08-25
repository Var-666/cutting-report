from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from app.config import Settings


class EmailConfigurationError(RuntimeError):
    pass


class EmailService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.smtp_enabled
            and self.settings.smtp_host
            and self.settings.smtp_from_email
        )

    def status(self) -> dict:
        return {
            "enabled": self.settings.smtp_enabled,
            "configured": self.configured,
            "host": self.settings.smtp_host or None,
            "port": self.settings.smtp_port,
            "from_email": self.settings.smtp_from_email or None,
            "use_tls": self.settings.smtp_use_tls,
            "use_ssl": self.settings.smtp_use_ssl,
        }

    def send(self, delivery: dict, attachment: bytes | None) -> str:
        if not self.configured:
            raise EmailConfigurationError("SMTP 尚未配置或未启用")

        message_id = make_msgid(domain=self.settings.smtp_from_email.split("@")[-1])
        message = EmailMessage()
        message["Message-ID"] = message_id
        message["From"] = formataddr(
            (self.settings.smtp_from_name, self.settings.smtp_from_email)
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

        smtp_class = smtplib.SMTP_SSL if self.settings.smtp_use_ssl else smtplib.SMTP
        with smtp_class(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=self.settings.smtp_timeout,
        ) as client:
            if not self.settings.smtp_use_ssl and self.settings.smtp_use_tls:
                client.starttls()
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, self.settings.smtp_password)
            client.send_message(message)
        return message_id
