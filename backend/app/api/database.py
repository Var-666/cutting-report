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
        bundle = request.app.state.query_service.fetch_report_data(filters)
    except SqlServerConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("database preview failed")
        raise HTTPException(status_code=500, detail="数据预览查询失败。") from exc

    records_by_dataset = {
        "summary": bundle.summary_records,
        "workshop": bundle.workshop_records,
        "equipment": bundle.equipment_records,
    }
    records = records_by_dataset[payload.dataset]
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
