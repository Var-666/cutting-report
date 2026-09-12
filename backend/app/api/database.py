from __future__ import annotations

import importlib.util
import hashlib
import logging
import os
import re
import secrets
import tempfile
from dataclasses import asdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.api.auth import require_admin
from app.config import settings
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
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


class DatabaseConfigurationRequest(BaseModel):
    data_source_mode: Literal["demo", "excel", "sqlserver"] = "demo"
    worker_database_auth_enabled: bool = False
    file_database_enabled: bool = False
    production: SavedConnectionRequest
    worker: SavedConnectionRequest
    file: SavedConnectionRequest


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


def _database_source_status() -> list[dict]:
    definitions = (
        ("production", "生产报表库", settings.effective_data_source_mode == "sqlserver"),
        ("worker", "工人 HR 库", settings.worker_database_auth_enabled),
        ("file", "切割文件库", settings.file_database_enabled),
    )
    sources = []
    for source, label, enabled in definitions:
        config = settings.sqlserver_connection(source)
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
    return sources


def _saved_connection(source: str) -> dict:
    prefix = "sqlserver" if source == "production" else f"{source}_sqlserver"
    common = settings.sqlserver_connection(source)
    password = str(getattr(settings, f"{prefix}_password"))
    return {
        "host": str(getattr(settings, f"{prefix}_host")),
        "port": int(getattr(settings, f"{prefix}_port")),
        "database": str(getattr(settings, f"{prefix}_database")),
        "username": str(getattr(settings, f"{prefix}_username")),
        "password_configured": bool(password),
        "driver": str(getattr(settings, f"{prefix}_driver") or common["driver"]),
        "encrypt": str(
            getattr(settings, f"{prefix}_encrypt") or common["encrypt"]
        ).strip().lower() in {"1", "true", "yes", "on"},
        "trust_server_certificate": str(
            getattr(settings, f"{prefix}_trust_server_certificate")
            or common["trust_server_certificate"]
        ).strip().lower() in {"1", "true", "yes", "on"},
    }


def _database_configuration_data() -> dict:
    return {
        "data_source_mode": settings.effective_data_source_mode,
        "worker_database_auth_enabled": settings.worker_database_auth_enabled,
        "file_database_enabled": settings.file_database_enabled,
        "production": _saved_connection("production"),
        "worker": _saved_connection("worker"),
        "file": _saved_connection("file"),
    }


def _dotenv_quote(value: object) -> str:
    text = str(value)
    if any(character in text for character in ("\r", "\n", "\x00")):
        raise ValueError("配置值不能包含换行或空字符")
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _write_runtime_env(values: dict[str, object]) -> None:
    path = settings.runtime_settings_env_path
    path.parent.mkdir(parents=True, exist_ok=True)
    original = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    key_pattern = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=")
    written: set[str] = set()
    output: list[str] = []
    for line in original:
        match = key_pattern.match(line)
        key = match.group(1) if match else None
        if key in values:
            output.append(f"{key}={_dotenv_quote(values[key])}")
            written.add(key)
        else:
            output.append(line)
    if output and output[-1] != "":
        output.append("")
    if not original:
        output.extend(
            [
                "# 由系统设置页管理。修改后需重启服务生效。",
                "# 文件中包含数据库密码，不要提交到代码库或对外发送。",
                "",
            ]
        )
    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={_dotenv_quote(value)}")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write("\n".join(output).rstrip() + "\n")
            temporary_path = Path(handle.name)
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _connection_env_values(
    source: str, payload: SavedConnectionRequest
) -> dict[str, object]:
    env_prefix = "SQLSERVER" if source == "production" else f"{source.upper()}_SQLSERVER"
    attribute_prefix = "sqlserver" if source == "production" else f"{source}_sqlserver"
    submitted_password = (
        payload.password.get_secret_value() if payload.password is not None else None
    )
    password = (
        submitted_password
        if submitted_password not in {None, ""}
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


@router.get("/configuration")
def get_database_configuration(request: Request) -> dict:
    return {
        "success": True,
        "data": _database_configuration_data(),
        "request_id": request.state.request_id,
    }


@router.put("/configuration")
def save_database_configuration(
    payload: DatabaseConfigurationRequest, request: Request
) -> dict:
    values: dict[str, object] = {
        "APP_DATA_SOURCE_MODE": payload.data_source_mode,
        "WORKER_DATABASE_AUTH_ENABLED": str(
            payload.worker_database_auth_enabled
        ).lower(),
        "FILE_DATABASE_ENABLED": str(payload.file_database_enabled).lower(),
    }
    for source in ("production", "worker", "file"):
        values.update(_connection_env_values(source, getattr(payload, source)))
    try:
        _write_runtime_env(values)
    except (OSError, ValueError) as exc:
        logger.exception("failed to save runtime database configuration")
        raise HTTPException(
            status_code=500,
            detail="配置保存失败，请检查 data 目录的写入权限。",
        ) from exc
    return {
        "success": True,
        "data": {
            "saved": True,
            "restart_required": True,
            "path": "data/settings.env",
            "message": "配置已安全保存，请重启服务后生效。",
        },
        "request_id": request.state.request_id,
    }


@router.get("/status")
def database_status(request: Request) -> dict:
    mappings = _query_mapping_status()
    mode = settings.effective_data_source_mode
    workbench_enabled = mode == "excel" or settings.sql_workbench_enabled
    excel_status = None
    excel_error = None
    if mode == "excel":
        try:
            excel_status = request.app.state.query_service.excel.status()
        except Exception as exc:
            excel_error = str(exc)
    return {
        "success": True,
        "data": {
            "mode": mode,
            "demo_mode": mode == "demo",
            "excel_status": excel_status,
            "excel_error": excel_error,
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
            "data_sources": _database_source_status(),
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
        validated = SqlWorkbenchService(settings).validate(payload.sql)
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


@router.post("/sql/execute")
def execute_sql(
    payload: SqlExecuteRequest,
    request: Request,
    workbench_token: str | None = Header(default=None, alias="X-SQL-Workbench-Token"),
) -> dict:
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
    elif mode != "excel":
        raise HTTPException(status_code=409, detail="演示数据模式不能执行 SQL 查询")

    query_hash = hashlib.sha256(payload.sql.encode("utf-8")).hexdigest()[:16]
    try:
        service = SqlWorkbenchService(settings)
        result = (
            service.execute_excel(
                request.app.state.query_service.excel,
                payload.sql,
                payload.max_rows,
            )
            if mode == "excel"
            else service.execute(payload.sql, payload.max_rows)
        )
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


@router.post("/test-connection")
def test_connection(payload: ConnectionTestRequest, request: Request) -> dict:
    temporary_settings = replace(
        settings,
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
            cursor = connection.cursor()
            cursor.execute("SELECT 1 AS connection_ok")
            row = cursor.fetchone()
        if row is None or int(row[0]) != 1:
            raise RuntimeError("数据库未返回预期结果")
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


@router.post("/preview")
def preview_dataset(payload: DatabasePreviewRequest, request: Request) -> dict:
    filters = ReportFilter(
        report_date=payload.report_date,
        project_code=payload.project_code,
        ship_no=payload.ship_no,
    )
    try:
        query_service = request.app.state.query_service
        if payload.dataset == "summary":
            records = query_service.fetch_summary_data(filters).summary_records
        elif payload.dataset == "workshop":
            records = query_service.fetch_workshop_data(filters).workshop_records
        else:
            records = query_service.fetch_equipment_data(filters).records
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("database preview failed")
        raise HTTPException(status_code=500, detail="数据预览查询失败。") from exc

    return {
        "success": True,
        "data": {
            "dataset": payload.dataset,
            "mode": settings.effective_data_source_mode,
            "items": to_primitive(records[: payload.limit]),
            "returned": min(len(records), payload.limit),
            "total": len(records),
            "truncated": len(records) > payload.limit,
        },
        "request_id": request.state.request_id,
    }
