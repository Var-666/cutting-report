from __future__ import annotations

import importlib.util
import hashlib
import logging
import secrets
from dataclasses import asdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.api.workloads import query_requests
from app.services.process_lock import settings_file_lock
from app.api.devices import device_state
from app.api.runtime import runtime_state
from app.api.auth import require_admin
from app.services.runtime_settings_store import read_runtime_env, write_runtime_env, runtime_settings_lock
from app.config import Settings, settings
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
from app.services.request_cancellation import SqlRequestCancelled, cancellable_cursor
from app.services.sql_workbench_service import (
    SqlWorkbenchService,
    SqlWorkbenchValidationError,
)


logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/database",
    tags=["database"],
    dependencies=[Depends(require_admin)],
)


class ConnectionTestRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=1433, ge=1, le=65535)
    database: str = Field(min_length=1, max_length=128)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=256)
    driver: str = Field(default="ODBC Driver 18 for SQL Server", max_length=128)
    encrypt: bool = True
    trust_server_certificate: bool = True

    @field_validator("host", "database", "username", "driver")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


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


class DatabasePreviewRequest(BaseModel):
    dataset: Literal["summary", "workshop", "equipment"]
    report_date: date
    project_code: str | None = Field(default=None, max_length=64)
    ship_no: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=50, ge=1, le=100)

    @field_validator("project_code", "ship_no")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class SqlValidateRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=50_000)


class SqlExecuteRequest(SqlValidateRequest):
    max_rows: int = Field(default=100, ge=1, le=500)


def _query_mapping_status() -> list[dict]:
    query_dir = Path(__file__).resolve().parents[1] / "database" / "queries"
    labels = {
        "leadership_report.sql": "领导报表日/月汇总",
        "leadership_projects.sql": "领导报表工程分布",
        "summary_base.sql": "领导汇总基础数据",
        "workshop.sql": "车间生产明细",
        "equipment.sql": "设备生产/能耗数据",
    }
    items = []
    for filename, label in labels.items():
        path = query_dir / filename
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        items.append(
            {
                "dataset": filename.removesuffix(".sql"),
                "label": label,
                "filename": filename,
                "ready": bool(content) and "TODO:" not in content,
            }
        )
    return items


def _database_source_status(configuration: Settings | None = None) -> list[dict]:
    configuration = configuration or settings
    definitions = (
        ("production", "生产报表库", configuration.effective_data_source_mode == "sqlserver"),
        ("worker", "工人 HR 库", configuration.worker_database_auth_enabled),
        ("file", "切割文件库", configuration.file_database_enabled),
    )
    sources = []
    for source, label, enabled in definitions:
        config = configuration.sqlserver_connection(source)
        configured = all(
            config[key] for key in ("host", "database", "username", "password")
        )
        sources.append(
            {
                "source": source,
                "label": label,
                "enabled": enabled,
                "configured": configured,
                "host": config["host"] or None,
                "port": config["port"],
                "database": config["database"] or None,
                "driver": config["driver"],
                "uses_primary_fallback": bool(config["uses_primary_fallback"]),
            }
        )
    sources.append({
        'source': 'welding', 'label': '小组立焊接线 · MySQL',
        'enabled': configuration.weld_enabled,
        'configured': all((configuration.weld_db_host, configuration.weld_db_name,
                           configuration.weld_db_user, configuration.weld_db_password)),
        'host': configuration.weld_db_host or None, 'port': configuration.weld_db_port,
        'database': configuration.weld_db_name or None, 'driver': 'MySQL Connector/Python',
        'uses_primary_fallback': False,
    })
    return sources


def _saved_connection(source: str, configuration: Settings | None = None) -> dict:
    configuration = configuration or settings
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


def _database_configuration_data(configuration: Settings | None = None) -> dict:
    configuration = configuration or settings
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


def _active_settings(request: Request) -> Settings:
    return getattr(runtime_state(request), "settings", settings) if hasattr(request, 'app') else settings


def _write_runtime_env(values: dict[str, object]) -> None:
    write_runtime_env(settings.runtime_settings_env_path, values)


def _connection_env_values(
    source: str, payload: SavedConnectionRequest
) -> dict[str, object]:
    env_prefix = "SQLSERVER" if source == "production" else f"{source.upper()}_SQLSERVER"
    attribute_prefix = "sqlserver" if source == "production" else f"{source}_sqlserver"
    submitted_password = (
        payload.password.get_secret_value() if payload.password is not None else None
    )
    saved_password = read_runtime_env(settings.runtime_settings_env_path).get(f"{env_prefix}_PASSWORD")
    password = (
        submitted_password
        if submitted_password not in {None, ""}
        else saved_password if saved_password is not None
        else getattr(settings, f"{attribute_prefix}_password")
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


def _welding_env_values(payload: WeldingConnectionRequest, configuration: Settings) -> dict[str, object]:
    password = payload.password.get_secret_value() if payload.password else ''
    saved = read_runtime_env(settings.runtime_settings_env_path).get('WELD_DB_PASSWORD', configuration.weld_db_password)
    return {'WELD_ENABLED': str(payload.enabled).lower(), 'WELD_DB_HOST': payload.host,
            'WELD_DB_PORT': payload.port, 'WELD_DB_NAME': payload.database,
            'WELD_DB_USER': payload.username, 'WELD_DB_PASSWORD': password or saved,
            'WELD_DB_TIMEZONE': payload.timezone}


@router.get("/configuration")
def get_database_configuration(request: Request) -> dict:
    return {
        "success": True,
        "data": _database_configuration_data(_active_settings(request)),
        "request_id": request.state.request_id,
    }


@router.put("/configuration")
def save_database_configuration(
    payload: DatabaseConfigurationRequest, request: Request
) -> dict:
    with runtime_settings_lock, settings_file_lock(settings.runtime_settings_env_path):
        current = _active_settings(request)
        if payload.welding is not None:
            unchanged = DatabaseConfigurationRequest.model_validate({**_database_configuration_data(current), 'welding': None})
            if payload.model_copy(update={'welding': None}) == unchanged:
                return _publish_database_configuration(request, _welding_env_values(payload.welding, current))
        values: dict[str, object] = {
            "APP_DATA_SOURCE_MODE": payload.data_source_mode,
            "WORKER_DATABASE_AUTH_ENABLED": str(
                payload.worker_database_auth_enabled
            ).lower(),
            "FILE_DATABASE_ENABLED": str(payload.file_database_enabled).lower(),
        }
        for source in ("production", "worker", "file"):
            values.update(_connection_env_values(source, getattr(payload, source)))
        if payload.welding is not None:
            values.update(_welding_env_values(payload.welding, current))
        return _publish_database_configuration(request, values)


@router.put('/welding/configuration')
def save_welding_configuration(payload: WeldingConnectionRequest, request: Request) -> dict:
    with runtime_settings_lock, settings_file_lock(settings.runtime_settings_env_path):
        return _publish_database_configuration(request, _welding_env_values(payload, _active_settings(request)))


def _publish_database_configuration(request: Request, values: dict[str, object]) -> dict:
    # Both callers hold the settings and file locks through prepare, save and publication.
    runtime = request.app.state.database_runtime
    try:
        merged = {**read_runtime_env(settings.runtime_settings_env_path), **values}
        prepared = runtime.prepare(merged)
    except ValueError:
        raise HTTPException(status_code=422, detail="数据库配置无效，请核对启用的数据源、连接参数及密码。") from None
    except Exception:
        raise HTTPException(status_code=503, detail="数据库配置暂无法加载，当前运行配置未改变。") from None
    try:
        _write_runtime_env(values)
    except (OSError, ValueError):
        logger.error("failed to save runtime database configuration")
        raise HTTPException(status_code=500, detail="配置保存失败，请检查 data 目录的写入权限。") from None
    runtime.publish(prepared, merged)
    return {
        "success": True,
        "data": {
            "saved": True,
            "restart_required": False,
            "path": "data/settings.env",
            "message": "配置已保存并热加载，Web 新请求立即生效，Worker 下一次操作自动加载；在途操作继续使用原配置。",
        },
        "request_id": request.state.request_id,
    }


@query_requests.route(router.post('/welding/test-connection'))
def test_welding_connection(payload: WeldingConnectionRequest, request: Request) -> dict:
    from app.database.welding import WeldingClient, WeldingUnavailable
    from app.services.database_runtime import DATABASE_SETTINGS
    try:
        base = _active_settings(request)
        changes = {DATABASE_SETTINGS[key]: value for key, value in _welding_env_values(payload, base).items()}
        changes['weld_enabled'] = True
        candidate = replace(base, **changes)
        client = WeldingClient(replace(candidate, weld_db_pool_size=1))
        started = perf_counter()
        with client.snapshot() as session:
            session.fetch('SELECT 1 AS connected', limit=1)
        return {'success': True, 'data': {'connected': True, 'elapsed_ms': round((perf_counter() - started) * 1000),
                'message': '只读事务连接测试成功；表结构、权限范围及指标仍需核验'}, 'request_id': request.state.request_id}
    except ValueError:
        raise HTTPException(status_code=422, detail='焊接连接参数不完整或无效，请核对账号、密码及时区') from None
    except WeldingUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/status")
def database_status(request: Request) -> dict:
    settings = _active_settings(request)
    mappings = _query_mapping_status()
    mode = settings.effective_data_source_mode
    workbench_enabled = mode == "sqlserver" and settings.sql_workbench_enabled
    return {
        "success": True,
        "data": {
            "mode": mode,
            "demo_mode": mode == "demo",
            "pyodbc_installed": importlib.util.find_spec("pyodbc") is not None,
            "connection_configured": all(
                [
                    settings.sqlserver_host,
                    settings.sqlserver_database,
                    settings.sqlserver_username,
                    settings.sqlserver_password,
                ]
            ),
            "configured_host": settings.sqlserver_host or None,
            "configured_database": settings.sqlserver_database or None,
            "driver": settings.sqlserver_driver,
            "data_sources": _database_source_status(settings),
            "query_mappings": mappings,
            "all_queries_ready": all(item["ready"] for item in mappings),
            "arbitrary_sql_enabled": workbench_enabled,
            "sql_workbench_enabled": workbench_enabled,
            "sql_workbench_requires_token": mode == "sqlserver",
            "sql_workbench_token_configured": bool(settings.sql_workbench_admin_token),
            "sql_workbench_max_rows": settings.sql_workbench_max_rows,
        },
        "request_id": request.state.request_id,
    }


@router.post("/sql/validate")
def validate_sql(payload: SqlValidateRequest, request: Request) -> dict:
    try:
        validated = SqlWorkbenchService(_active_settings(request)).validate(payload.sql)
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "success": True,
        "data": {
            "valid": True,
            "formatted_sql": validated.formatted_sql,
            "tables": validated.tables,
            "columns": validated.columns,
            "read_only": True,
        },
        "request_id": request.state.request_id,
    }


@query_requests.route(router.post("/sql/execute"))
def execute_sql(
    payload: SqlExecuteRequest,
    request: Request,
    workbench_token: str | None = Header(default=None, alias="X-SQL-Workbench-Token"),
) -> dict:
    settings = _active_settings(request)
    mode = settings.effective_data_source_mode
    if mode == "sqlserver":
        if not settings.sql_workbench_enabled:
            raise HTTPException(status_code=403, detail="SQL 工作台执行功能尚未启用")
        if not settings.sql_workbench_admin_token:
            raise HTTPException(status_code=503, detail="SQL 工作台管理员令牌尚未配置")
        if not workbench_token or not secrets.compare_digest(
            workbench_token, settings.sql_workbench_admin_token
        ):
            raise HTTPException(status_code=401, detail="SQL 工作台管理员令牌无效")
    else:
        raise HTTPException(status_code=409, detail="演示数据模式不能执行 SQL 查询")

    query_hash = hashlib.sha256(payload.sql.encode("utf-8")).hexdigest()[:16]
    try:
        service = SqlWorkbenchService(settings)
        result = service.execute(payload.sql, payload.max_rows)
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SqlRequestCancelled:
        raise
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning(
            "SQL workbench query failed query_hash=%s error_type=%s",
            query_hash,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="查询执行失败，请检查 SQL、字段名称和数据源状态。",
        ) from exc
    logger.info(
        "SQL workbench query completed query_hash=%s rows=%s elapsed_ms=%s",
        query_hash,
        result.returned,
        result.elapsed_ms,
    )
    return {
        "success": True,
        "data": asdict(result),
        "request_id": request.state.request_id,
    }


@query_requests.route(router.post("/test-connection"))
def test_connection(payload: ConnectionTestRequest, request: Request) -> dict:
    temporary_settings = replace(
        _active_settings(request),
        sqlserver_host=payload.host,
        sqlserver_port=payload.port,
        sqlserver_database=payload.database,
        sqlserver_username=payload.username,
        sqlserver_password=payload.password.get_secret_value(),
        sqlserver_driver=payload.driver,
        sqlserver_encrypt="yes" if payload.encrypt else "no",
        sqlserver_trust_server_certificate=(
            "yes" if payload.trust_server_certificate else "no"
        ),
    )
    started = perf_counter()
    try:
        client = SqlServerClient(temporary_settings)
        with client.connect() as connection:
            with cancellable_cursor(connection) as cursor:
                cursor.execute("SELECT 1 AS connection_ok")
                row = cursor.fetchone()
        if row is None or int(row[0]) != 1:
            raise RuntimeError("数据库未返回预期结果")
    except SqlRequestCancelled:
        raise
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("SQL Server connection test failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="连接失败，请检查服务器地址、端口、数据库、账号、证书和网络配置。",
        ) from exc
    return {
        "success": True,
        "data": {
            "connected": True,
            "database": payload.database,
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
            "saved": False,
            "message": "连接测试成功。本次凭据未保存。",
        },
        "request_id": request.state.request_id,
    }


@query_requests.route(router.post("/preview", dependencies=[Depends(device_state)]))
def preview_dataset(payload: DatabasePreviewRequest, request: Request) -> dict:
    filters = ReportFilter(
        report_date=payload.report_date,
        project_code=payload.project_code,
        ship_no=payload.ship_no,
    )
    try:
        query_service = device_state(request).query_service
        if payload.dataset == "summary":
            records = query_service.fetch_summary_data(filters).summary_records
        elif payload.dataset == "workshop":
            records = query_service.fetch_workshop_data(filters).workshop_records
        else:
            records = query_service.fetch_equipment_data(filters).records
    except SqlRequestCancelled:
        raise
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("database preview failed")
        raise HTTPException(status_code=500, detail="数据预览查询失败。") from exc

    return {
        "success": True,
        "data": {
            "dataset": payload.dataset,
            "mode": device_state(request).settings.effective_data_source_mode,
            "items": to_primitive(records[: payload.limit]),
            "returned": min(len(records), payload.limit),
            "total": len(records),
            "truncated": len(records) > payload.limit,
        },
        "request_id": request.state.request_id,
    }
