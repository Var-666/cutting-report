"""Connection contracts for additional external sources; no business SQL lives here."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class DatabaseSourceFields(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)

    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    name: str = Field(min_length=1, max_length=128)
    engine: Literal['sqlserver', 'mysql']
    enabled: bool = True
    host: str = Field(min_length=0, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=0, max_length=128)
    username: str = Field(min_length=0, max_length=128)
    connect_timeout: int = Field(default=5, ge=1, le=60)
    query_timeout: int = Field(default=120, ge=1, le=300)
    driver: str = Field(default='ODBC Driver 18 for SQL Server', min_length=1, max_length=128)
    encrypt: bool = True
    trust_server_certificate: bool = True
    legacy_tls: bool = False
    pool_size: int = Field(default=4, ge=1, le=4)
    sql_max_concurrent: int | None = Field(default=None, ge=1, le=64)
    charset: str = Field(default='utf8mb4', pattern=r'^[a-zA-Z0-9_]{1,32}$')
    readonly: Literal[True] = True
    isolation_level: Literal['REPEATABLE READ'] = 'REPEATABLE READ'

    @field_validator('name', 'host', 'database', 'username', 'driver')
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError('连接字段不能为空或包含控制字符')
        return value

    @model_validator(mode='after')
    def validate_legacy_tls(self) -> 'DatabaseSourceFields':
        if self.enabled and not all((self.host, self.database, self.username)):
            raise ValueError('启用的数据源必须填写完整连接参数')
        if self.legacy_tls and self.engine != 'sqlserver':
            raise ValueError('旧版 TLS 兼容仅适用于 SQL Server')
        return self


class DatabaseSourceRequest(DatabaseSourceFields):
    password: SecretStr | None = Field(default=None, max_length=256)


class DatabaseSourceConfig(DatabaseSourceFields):
    password: SecretStr = Field(default=SecretStr(''), max_length=256)

    def sqlserver_connection(self) -> dict:
        if self.engine != 'sqlserver':
            raise ValueError('数据源不是 SQL Server')
        return {**self.model_dump(exclude={'password'}),
                'password': self.password.get_secret_value(),
                'encrypt': 'yes' if self.encrypt else 'no',
                'trust_server_certificate': 'yes' if self.trust_server_certificate else 'no'}


class DatabaseSourceView(DatabaseSourceFields):
    password_configured: bool
    configured: bool
    references: list[str]


class SourceEnabledRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool


class SourceDeleted(BaseModel):
    deleted: bool
