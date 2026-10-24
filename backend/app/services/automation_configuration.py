from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class AutomationConfigurationRequest(BaseModel):
    automation_enabled: bool = True
    automation_scan_seconds: float = Field(default=15, ge=1, le=3600)
    email_retry_base_seconds: int = Field(default=60, ge=1, le=86400)
    smtp_enabled: bool = False
    smtp_host: str = Field(default="", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str = Field(default="", max_length=255)
    smtp_password: SecretStr | None = Field(default=None, max_length=256)
    smtp_from_email: str = Field(default="", max_length=320)
    smtp_from_name: str = Field(default="切割生产智能产报系统", max_length=100)
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = Field(default=20, ge=1, le=300)

    @field_validator(
        "smtp_host", "smtp_username", "smtp_from_email", "smtp_from_name"
    )
    @classmethod
    def normalize_configuration_text(cls, value: str) -> str:
        cleaned = value.strip()
        if any(character in cleaned for character in ("\r", "\n", "\x00")):
            raise ValueError("不能包含换行或空字符")
        return cleaned

    @model_validator(mode="after")
    def validate_smtp_configuration(self):
        if self.smtp_use_tls and self.smtp_use_ssl:
            raise ValueError("STARTTLS 与 SSL 不能同时启用")
        if self.smtp_enabled:
            if not self.smtp_host:
                raise ValueError("启用 SMTP 后必须填写服务器地址")
            if (
                not self.smtp_from_email
                or "@" not in self.smtp_from_email
                or self.smtp_from_email.startswith("@")
                or self.smtp_from_email.endswith("@")
            ):
                raise ValueError("启用 SMTP 后必须填写有效的发件人邮箱")
        return self
