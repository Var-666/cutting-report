"""Database configuration contracts and mappings; publication stays in DatabaseRuntime."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.config import Settings
from app.models.database_sources import DatabaseSourceConfig


class SavedConnectionRequest(BaseModel):
    host: str = Field(default="", max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(default="", max_length=128)
    username: str = Field(default="", max_length=128)
    password: SecretStr | None = Field(default=None, max_length=256)
    driver: str = Field(default="ODBC Driver 18 for SQL Server", max_length=128)
    encrypt: bool = True
    trust_server_certificate: bool = True

    @field_validator("host", "database", "username", "driver")
    @classmethod
    def clean_saved_text(cls, value: str) -> str:
        cleaned = value.strip()
        if any(character in cleaned for character in ("\r", "\n", "\x00")):
            raise ValueError("不能包含换行或空字符")
        return cleaned


class WeldingConnectionRequest(BaseModel):
    enabled: bool = False
    host: str = Field(default='', max_length=255)
    port: int = Field(default=3306, ge=1, le=65535)
    database: str = Field(default='weld_production_data', max_length=128)
    username: str = Field(default='', max_length=128)
    password: SecretStr | None = Field(default=None, max_length=256)
    timezone: str = Field(default='Asia/Shanghai', max_length=64)

    @field_validator('host', 'database', 'username', 'timezone')
    @classmethod
    def clean_text(cls, value: str) -> str:
        if any(char in value for char in ('\r', '\n', '\x00')):
            raise ValueError('不能包含控制字符')
        return value.strip()

    @field_validator('timezone')
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError('生产时区无效') from None
        return value


class DatabaseConfigurationRequest(BaseModel):
    data_source_mode: Literal["demo", "sqlserver"] = "demo"
    worker_database_auth_enabled: bool = False
    file_database_enabled: bool = False
    production: SavedConnectionRequest
    worker: SavedConnectionRequest
    file: SavedConnectionRequest
    welding: WeldingConnectionRequest | None = None


def database_configuration_data(configuration: Settings, line_id: str | None = None) -> dict:
    from app.database.source_registry import DatabaseSourceRegistry
    from app.device_config import load_line_catalog
    registry = DatabaseSourceRegistry.from_json(configuration.database_sources_json)
    catalog = load_line_catalog(configuration.production_lines_json)
    line = catalog.get(line_id) if line_id else None

    def profile(key: str) -> dict:
        if not key:
            return {"host": "", "port": 1433, "database": "", "username": "", "password_configured": False,
                    "driver": "ODBC Driver 18 for SQL Server", "encrypt": True, "trust_server_certificate": True}
        config = registry.get(key)
        return {**config.model_dump(include={"host", "port", "database", "username", "driver", "encrypt", "trust_server_certificate"}),
                "password_configured": bool(config.password.get_secret_value())}

    welding = None
    if line and line.type == "welding":
        welding = {**profile(line.source_key), "enabled": line.enabled, "timezone": line.timezone}
        for key in ("driver", "encrypt", "trust_server_certificate"):
            welding.pop(key)
    return {"data_source_mode": configuration.effective_data_source_mode,
            "worker_database_auth_enabled": configuration.worker_database_auth_enabled,
            "file_database_enabled": configuration.file_database_enabled,
            "production": profile(line.source_key if line and line.type == "cutting" else ""),
            "worker": profile(configuration.worker_source_key), "file": profile(configuration.file_source_key),
            "welding": welding}


class SourceConnectionError(RuntimeError):
    pass


def test_source_connection(config: DatabaseSourceConfig) -> dict:
    """Probe only SELECT 1; connection success says nothing about business schemas."""
    from time import perf_counter
    from app.database.sqlserver import SqlServerClient
    from app.database.mysql import MySqlClient, MySqlUnavailable
    from app.core.request_cancellation import SqlRequestCancelled

    started = perf_counter()
    client = None
    try:
        if config.engine == 'sqlserver':
            rows = SqlServerClient(config).fetch_all('SELECT 1 AS connected', ())
        else:
            client = MySqlClient(config.model_copy(update={'pool_size': 1}))
            with client.snapshot() as session:
                rows = session.fetch('SELECT 1 AS connected', limit=1)
        if not rows or rows[0].get('connected') != 1:
            raise SourceConnectionError('数据库未返回预期结果')
    except SqlRequestCancelled:
        raise
    except MySqlUnavailable as exc:
        raise SourceConnectionError(str(exc)) from None
    except Exception:
        raise SourceConnectionError('连接失败，请核对驱动、地址、端口、账号、证书及网络配置') from None
    finally:
        if client is not None:
            client.close()
    return {'connected': True, 'saved': False, 'database': config.database,
        'elapsed_ms': round((perf_counter() - started) * 1000, 2),
        'message': '只读连接测试成功，参数未保存；业务表、字段及统计口径尚未验证。'}
