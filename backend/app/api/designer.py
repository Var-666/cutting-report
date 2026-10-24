from __future__ import annotations

import hmac
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.workloads import query_requests
from app.api.auth import require_admin
from app.api.devices import device_state, device_settings
from app.config import settings
from app.api.runtime import runtime_state
from app.models.designer import (
    DefinitionUpsert,
    QueryPreviewRequest,
    QueryTaskUpsert,
    TemplateUpsert,
)
from app.services.custom_report_service import CustomReportService
from app.services.sql_workbench_service import SqlWorkbenchValidationError


router = APIRouter(
    prefix="/api/v1/designer",
    tags=["report-designer"],
    dependencies=[Depends(require_admin)],
)
AdminToken = Annotated[str | None, Header(alias="X-SQL-Workbench-Token")]


class SqlTextRequest(BaseModel):
    sql_text: str = Field(min_length=1, max_length=50_000)


def _envelope(request: Request, data):
    return {"success": True, "data": data, "request_id": request.state.request_id}


def _require_admin_token(token: str | None, request=None) -> None:
    configuration = getattr(runtime_state(request), "settings", settings) if request is not None else settings
    if configuration.effective_data_source_mode != "sqlserver":
        return
    expected = settings.sql_workbench_admin_token
    if not expected:
        raise HTTPException(status_code=503, detail="服务器尚未配置报表设计管理员令牌")
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="管理员令牌无效")


def _translate_write_error(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=404, detail="配置不存在") from exc
    if isinstance(exc, sqlite3.IntegrityError):
        raise HTTPException(status_code=409, detail="配置编码已存在，请更换编码") from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise exc


@router.get("/published-definitions")
def published_definitions(request: Request):
    items = request.app.state.definitions.list_definitions(
        enabled_only=True, include_builtin=False
    )
    return _envelope(request, {"items": items})


@router.get("/query-tasks")
def list_query_tasks(request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    return _envelope(request, {"items": request.app.state.definitions.list_query_tasks()})


@router.post("/query-tasks/validate")
def validate_query_task(payload: SqlTextRequest, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        result = CustomReportService(settings).validate(payload.sql_text)
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _envelope(request, result)


@router.post("/query-tasks", status_code=201)
def create_query_task(payload: QueryTaskUpsert, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        CustomReportService(settings).validate(payload.sql_text)
        item = request.app.state.definitions.save_query_task(payload.model_dump())
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, item)


@router.put("/query-tasks/{query_task_id}")
def update_query_task(query_task_id: str, payload: QueryTaskUpsert, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        CustomReportService(settings).validate(payload.sql_text)
        item = request.app.state.definitions.save_query_task(payload.model_dump(), query_task_id)
    except SqlWorkbenchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, item)


@router.delete("/query-tasks/{query_task_id}")
def delete_query_task(query_task_id: str, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        request.app.state.definitions.delete_query_task(query_task_id)
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, {"deleted": True})


@query_requests.route(router.post("/query-tasks/{query_task_id}/preview", dependencies=[Depends(device_state)]))
def preview_query_task(query_task_id: str, payload: QueryPreviewRequest, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    task = request.app.state.definitions.get_query_task(query_task_id)
    if not task:
        raise HTTPException(status_code=404, detail="查询任务不存在")
    try:
        rows = CustomReportService(device_settings(request)).execute(
            task,
            report_date=payload.report_date,
            period_type="day",
            project_code=payload.project_code,
            ship_no=payload.ship_no,
            parameters=payload.parameters,
            max_rows=payload.max_rows,
        )
    except (SqlWorkbenchValidationError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _envelope(
        request,
        {"columns": task["columns"], "items": rows, "returned": len(rows), "mode": device_settings(request).effective_data_source_mode},
    )


@router.get("/templates")
def list_templates(request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    return _envelope(request, {"items": request.app.state.definitions.list_templates()})


@router.post("/templates", status_code=201)
def create_template(payload: TemplateUpsert, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        item = request.app.state.definitions.save_template(payload.model_dump())
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, item)


@router.put("/templates/{template_id}")
def update_template(template_id: str, payload: TemplateUpsert, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        item = request.app.state.definitions.save_template(payload.model_dump(), template_id)
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, item)


@router.delete("/templates/{template_id}")
def delete_template(template_id: str, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        request.app.state.definitions.delete_template(template_id)
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, {"deleted": True})


@router.get("/definitions")
def list_definitions(request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    return _envelope(request, {"items": request.app.state.definitions.list_definitions()})


@router.post("/definitions", status_code=201)
def create_definition(payload: DefinitionUpsert, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        item = request.app.state.definitions.save_definition(payload.model_dump())
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, item)


@router.put("/definitions/{definition_id}")
def update_definition(definition_id: str, payload: DefinitionUpsert, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        item = request.app.state.definitions.save_definition(payload.model_dump(), definition_id)
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, item)


@router.delete("/definitions/{definition_id}")
def delete_definition(definition_id: str, request: Request, token: AdminToken = None):
    _require_admin_token(token, request)
    try:
        request.app.state.definitions.delete_definition(definition_id)
    except Exception as exc:
        _translate_write_error(exc)
    return _envelope(request, {"deleted": True})
