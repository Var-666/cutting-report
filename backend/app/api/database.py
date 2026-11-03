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

from app.core.report_deadline import ReportOperationTimeout
from app.core.sql_capacity import SqlCapacityError
from app.api.workloads import query_requests
from app.api.devices import device_state
from app.api.runtime import application_state, runtime_state
from app.api.auth import require_admin
from app.services.runtime_settings_store import RuntimeSettingsSaveError
from app.services.database_configuration import (
    DatabaseConfigurationRequest, SavedConnectionRequest, WeldingConnectionRequest,
    database_configuration_data,
)
from app.config import Settings, settings
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.domain import ReportFilter, to_primitive
from app.models.contracts import ApiEnvelope, Items
from app.models.database_sources import DatabaseSourceConfig, DatabaseSourceRequest, DatabaseSourceView, SourceEnabledRequest, SourceDeleted
from app.database.source_registry import SourceNotFound, SourceConflict
from app.services.database_configuration import test_source_connection, SourceConnectionError
from app.models.configuration_contracts import DatabaseConfiguration, ConfigurationSaved, DatabaseStatus, ConnectionTestResult, DatabasePreview, ValidatedWorkbenchSql, WorkbenchQueryResult
from app.models.resource_contracts import ResourceReport
from app.core.request_cancellation import SqlRequestCancelled, cancellable_cursor
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


@router.get('/resources', response_model=ApiEnvelope[ResourceReport])
def resource_status(request: Request) -> dict:
    from app.api.workloads import analysis_requests, authentication_requests, pdf_requests, snapshot_requests
    context = runtime_state(request)
    worker = application_state(request).worker_status_reader.status()
    return {'success': True, 'data': {
        'web': context.database_sources.capacity.status(context.database_sources.configs()),
        'http': [pool.status() for pool in (query_requests, snapshot_requests, analysis_requests, pdf_requests, authentication_requests)],
        'worker': worker.get('sql_resources'), 'worker_state': worker.get('state', 'unknown')},
        'request_id': request.state.request_id}


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
    from app.database.source_registry import DatabaseSourceRegistry
    registry = DatabaseSourceRegistry.from_json(configuration.database_sources_json)
    return [{"source": view.id, "label": view.name, "enabled": view.enabled, "configured": view.configured,
             "host": view.host or None, "port": view.port, "database": view.database or None,
             "driver": view.driver if view.engine == "sqlserver" else "MySQL Connector/Python", "uses_primary_fallback": False}
            for view in registry.list()]


def _active_settings(request: Request) -> Settings:
    return getattr(runtime_state(request), "settings", settings) if hasattr(request, 'app') else settings


@router.get("/configuration", response_model=None, responses={200: {"model": ApiEnvelope[DatabaseConfiguration]}})
def get_database_configuration(request: Request) -> dict:
    try:
        data = database_configuration_data(_active_settings(request), request.headers.get("X-Production-Line"))
    except ValueError:
        raise HTTPException(status_code=422, detail="产线上下文或数据库配置无效") from None
    return {
        "success": True,
        "data": data,
        "request_id": request.state.request_id,
    }


@router.put("/configuration", response_model=None, responses={200: {"model": ApiEnvelope[ConfigurationSaved]}})
def save_database_configuration(
    payload: DatabaseConfigurationRequest, request: Request
) -> dict:
    return _save_configuration(payload, request)


@router.put('/welding/configuration', response_model=None, responses={200: {'model': ApiEnvelope[ConfigurationSaved]}})
def save_welding_configuration(payload: WeldingConnectionRequest, request: Request) -> dict:
    return _save_configuration(payload, request)


def _save_configuration(payload, request: Request) -> dict:
    try:
        application_state(request).database_runtime.save_configuration(payload, line_id=request.headers.get("X-Production-Line"))
    except RuntimeSettingsSaveError:
        logger.error("failed to save runtime database configuration")
        raise HTTPException(status_code=500, detail="配置保存失败，请检查 data 目录的写入权限。") from None
    except ValueError:
        raise HTTPException(status_code=422, detail="数据库配置无效，请核对启用的数据源、连接参数及密码。") from None
    except Exception:
        raise HTTPException(status_code=503, detail="数据库配置暂无法加载，当前运行配置未改变。") from None
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


@query_requests.route(router.post('/welding/test-connection', response_model=None, responses={200: {'model': ApiEnvelope[ConnectionTestResult]}}))
def test_welding_connection(payload: WeldingConnectionRequest, request: Request) -> dict:
    context = runtime_state(request)
    line_id = request.headers.get("X-Production-Line")
    try:
        line = context.catalog.get(line_id)
        if line.type != "welding":
            raise ValueError("请选择焊接产线")
        old = context.database_sources.get(line.source_key)
        candidate = DatabaseSourceRequest.model_validate({**old.model_dump(exclude={"password"}),
            **payload.model_dump(exclude={"enabled", "timezone"}), "enabled": True})
        config = application_state(request).database_runtime.source_for_test(candidate)
        result = test_source_connection(config, capacity=context.database_sources.capacity)
    except ValueError:
        raise HTTPException(status_code=422, detail="焊接连接参数或产线上下文无效") from None
    except SourceConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"success": True, "data": result, "request_id": request.state.request_id}


@router.get("/status", response_model=None, responses={200: {"model": ApiEnvelope[DatabaseStatus]}})
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
            "connection_configured": all(item["configured"] for item in _database_source_status(settings) if item["enabled"]),
            "configured_host": None,
            "configured_database": None,
            "driver": "按数据源配置",
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


@router.post("/sql/validate", response_model=None, responses={200: {"model": ApiEnvelope[ValidatedWorkbenchSql]}})
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


@query_requests.route(router.post("/sql/execute", response_model=None, responses={200: {"model": ApiEnvelope[WorkbenchQueryResult]}}))
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
        service = SqlWorkbenchService(settings, client=device_state(request).query_service.client)
        result = service.execute(payload.sql, payload.max_rows)
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
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


@query_requests.route(router.post("/test-connection", response_model=None, responses={200: {"model": ApiEnvelope[ConnectionTestResult]}}))
def test_connection(payload: ConnectionTestRequest, request: Request) -> dict:
    temporary_config = DatabaseSourceConfig(id="connection-test", name="连接测试", engine="sqlserver",
        **payload.model_dump())
    started = perf_counter()
    try:
        client = SqlServerClient(temporary_config, capacity=runtime_state(request).database_sources.capacity, probe=True)
        with client.connect() as connection:
            with cancellable_cursor(connection) as cursor:
                cursor.execute("SELECT 1 AS connection_ok")
                row = cursor.fetchone()
        if row is None or int(row[0]) != 1:
            raise RuntimeError("数据库未返回预期结果")
    except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
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


@query_requests.route(router.post("/preview", dependencies=[Depends(device_state)], response_model=None, responses={200: {"model": ApiEnvelope[DatabasePreview]}}))
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
    except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
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


def _source_result(request: Request, action) -> dict:
    try:
        data = action()
    except SourceNotFound:
        raise HTTPException(status_code=404, detail='数据源不存在') from None
    except SourceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except RuntimeSettingsSaveError:
        raise HTTPException(status_code=500, detail='数据源保存失败，请检查 data 目录写入权限') from None
    except SourceConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except (SqlRequestCancelled, SqlCapacityError, ReportOperationTimeout):
        raise
    except ValueError:
        raise HTTPException(status_code=422, detail='数据源配置无效，请核对 ID、连接参数及密码') from None
    except Exception:
        raise HTTPException(status_code=503, detail='数据源配置暂不可用，已保留原运行配置') from None
    return {'success': True, 'data': data, 'request_id': request.state.request_id}


@router.get('/sources', response_model=ApiEnvelope[Items[DatabaseSourceView]])
def list_database_sources(request: Request):
    return _source_result(request, lambda: {'items': runtime_state(request).database_sources.list()})


@router.get('/sources/{source_id}', response_model=ApiEnvelope[DatabaseSourceView])
def get_database_source(source_id: str, request: Request):
    return _source_result(request, lambda: runtime_state(request).database_sources.view(source_id))


@router.post('/sources', response_model=ApiEnvelope[DatabaseSourceView], status_code=201)
def create_database_source(payload: DatabaseSourceRequest, request: Request):
    def save():
        runtime = application_state(request).database_runtime
        runtime.save_source(payload, create=True)
        return runtime.current_context.database_sources.view(payload.id)
    return _source_result(request, save)


@router.put('/sources/{source_id}', response_model=ApiEnvelope[DatabaseSourceView])
def update_database_source(source_id: str, payload: DatabaseSourceRequest, request: Request):
    def save():
        if source_id != payload.id:
            raise ValueError('数据源 ID 不允许修改')
        runtime = application_state(request).database_runtime
        runtime.save_source(payload)
        return runtime.current_context.database_sources.view(source_id)
    return _source_result(request, save)


@router.patch('/sources/{source_id}/enabled', response_model=ApiEnvelope[DatabaseSourceView])
def enable_database_source(source_id: str, payload: SourceEnabledRequest, request: Request):
    def save():
        runtime = application_state(request).database_runtime
        runtime.set_source_enabled(source_id, payload.enabled)
        return runtime.current_context.database_sources.view(source_id)
    return _source_result(request, save)


@router.delete('/sources/{source_id}', response_model=ApiEnvelope[SourceDeleted])
def delete_database_source(source_id: str, request: Request):
    def remove():
        application_state(request).database_runtime.delete_source(source_id)
        return {'deleted': True}
    return _source_result(request, remove)


@query_requests.route(router.post('/sources/test-connection', response_model=ApiEnvelope[ConnectionTestResult]))
def test_database_source(payload: DatabaseSourceRequest, request: Request):
    return _source_result(request, lambda: test_source_connection(
        application_state(request).database_runtime.source_for_test(payload),
        capacity=runtime_state(request).database_sources.capacity))
