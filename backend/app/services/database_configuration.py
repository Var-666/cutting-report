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


def _saved_connection(source: str, configuration: Settings) -> dict:
    prefix = "sqlserver" if source == "production" else f"{source}_sqlserver"
    common = configuration.sqlserver_connection(source)
    password = str(getattr(configuration, f"{prefix}_password"))
    return {
        "host": str(getattr(configuration, f"{prefix}_host")),
        "port": int(getattr(configuration, f"{prefix}_port")),
        "database": str(getattr(configuration, f"{prefix}_database")),
        "username": str(getattr(configuration, f"{prefix}_username")),
        "password_configured": bool(password),
        "driver": str(getattr(configuration, f"{prefix}_driver") or common["driver"]),
        "encrypt": str(
            getattr(configuration, f"{prefix}_encrypt") or common["encrypt"]
        ).strip().lower() in {"1", "true", "yes", "on"},
        "trust_server_certificate": str(
            getattr(configuration, f"{prefix}_trust_server_certificate")
            or common["trust_server_certificate"]
        ).strip().lower() in {"1", "true", "yes", "on"},
    }


def database_configuration_data(configuration: Settings) -> dict:
    return {
        "data_source_mode": configuration.effective_data_source_mode,
        "worker_database_auth_enabled": configuration.worker_database_auth_enabled,
        "file_database_enabled": configuration.file_database_enabled,
        "production": _saved_connection("production", configuration),
        "worker": _saved_connection("worker", configuration),
        "file": _saved_connection("file", configuration),
        "welding": {
            'enabled': configuration.weld_enabled, 'host': configuration.weld_db_host,
            'port': configuration.weld_db_port, 'database': configuration.weld_db_name,
            'username': configuration.weld_db_user, 'password_configured': bool(configuration.weld_db_password),
            'timezone': configuration.weld_db_timezone,
        },
    }


def connection_env_values(
    source: str, payload: SavedConnectionRequest, configuration: Settings, saved: dict
) -> dict[str, object]:
    env_prefix = "SQLSERVER" if source == "production" else f"{source.upper()}_SQLSERVER"
    attribute_prefix = "sqlserver" if source == "production" else f"{source}_sqlserver"
    submitted_password = (
        payload.password.get_secret_value() if payload.password is not None else None
    )
    saved_password = saved.get(f"{env_prefix}_PASSWORD")
    password = (
        submitted_password
        if submitted_password not in {None, ""}
        else saved_password if saved_password is not None
        else getattr(configuration, f"{attribute_prefix}_password")
    )
    return {
        f"{env_prefix}_HOST": payload.host,
        f"{env_prefix}_PORT": payload.port,
        f"{env_prefix}_DATABASE": payload.database,
        f"{env_prefix}_USERNAME": payload.username,
        f"{env_prefix}_PASSWORD": password,
        f"{env_prefix}_DRIVER": payload.driver,
        f"{env_prefix}_ENCRYPT": "yes" if payload.encrypt else "no",
        f"{env_prefix}_TRUST_SERVER_CERTIFICATE": (
            "yes" if payload.trust_server_certificate else "no"
        ),
    }


def welding_env_values(payload: WeldingConnectionRequest, configuration: Settings, saved: dict) -> dict[str, object]:
    password = payload.password.get_secret_value() if payload.password else ''
    saved_password = saved.get('WELD_DB_PASSWORD')
    if saved_password is None:
        saved_password = configuration.weld_db_password
    return {'WELD_ENABLED': str(payload.enabled).lower(), 'WELD_DB_HOST': payload.host,
            'WELD_DB_PORT': payload.port, 'WELD_DB_NAME': payload.database,
            'WELD_DB_USER': payload.username, 'WELD_DB_PASSWORD': password or saved_password,
            'WELD_DB_TIMEZONE': payload.timezone}


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
