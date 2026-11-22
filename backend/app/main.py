from __future__ import annotations

from starlette.concurrency import run_in_threadpool

import logging
import uuid
from time import monotonic
from contextlib import asynccontextmanager
from functools import cache, partial

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from app.services.frontend_static import FrontendAssets
from app.services.production_lines import supports_path

from app.api.configuration import router as configuration_router
from app.api.database import router as database_router
from app.api.analysis import router as analysis_router
from app.api.automation import router as automation_router
from app.modules.cutting.routes import router as cutting_router, callback_router as magic_box_callback_router
from app.api.downloads import router as downloads_router
from app.api.conditions import router as conditions_router
from app.api.auth import SESSION_COOKIE, router as auth_router
from app.api.alarms import router as alarms_router
from app.api.database_monitors import router as database_monitors_router
from app.api.operations import router as operations_router
from app.api.llm_usage import router as llm_usage_router
from app.api.designer import router as designer_router
from app.modules.cutting.equipment_routes import router as equipment_router
from app.modules.energy.routes import router as energy_router
from app.modules.cutting.leadership_routes import router as leadership_router
from app.modules.cutting.options_routes import router as options_router
from app.api.reports import router as reports_router
from app.api.reports import view_router
from app.api.requests import REQUEST_ID_PATTERN, router as requests_router
from app.modules.cutting.workshop_routes import router as workshop_router
from app.config import Settings
from app.api.devices import router as devices_router
from app.api.runtime import application_state, request_settings, runtime_state
from app.api.workloads import build_workloads
from app.modules.welding.routes import router as welding_router
from app.api.production_lines import router as production_lines_router
from app.api.pdm_statistics import router as pdm_statistics_router
from app.services.application_runtime import build_runtime
from app.core.operations_logging import configured_logging, event, operation_context
from app.core.sql_capacity import SqlCapacityError, sql_identity
from app.core.report_deadline import ReportOperationTimeout
from app.core.process_lock import FileLock
from app.services.worker_status import background_ready, public_status
from app.core.request_cancellation import (
    SqlRequestCancelled, bind_request_id, cancellation_session_scope,
    reset_request_id, sql_request_cancellations,
)
from anyio import CapacityLimiter, to_thread

_health_threads = CapacityLimiter(2)


logger = logging.getLogger(__name__)


def _redact_sso_callback(record: logging.LogRecord) -> bool:
    # Uvicorn includes query strings in access logs; authorization codes are credentials.
    if isinstance(record.args, tuple) and len(record.args) == 5:
        path = str(record.args[2]).partition('?')[0]
        if path.endswith('/api/v1/auth/sso/callback'):
            record.args = (*record.args[:2], path, *record.args[3:])
    return True


logging.getLogger('uvicorn.access').addFilter(_redact_sso_callback)


@asynccontextmanager
async def lifespan(app: FastAPI, *, settings: Settings):
    # Dispatch recovery is safe only after the previous Web process has exited.
    lock = FileLock(settings.data_dir / ".web-process.lock")
    if not lock.acquire(blocking=False):
        raise RuntimeError("已有 Web 进程运行；请保持单 Web，后台角色独立启动")
    try:
        with configured_logging(settings.logs_dir, 'web', 'web', settings.operations_log_retention_days) as logs:
            runtime = build_runtime(settings, role="web")
            app.state.runtime = runtime
            app.state.workloads = build_workloads(settings, runtime.database_runtime.current_context.database_sources.capacity)
            try:
                with runtime.operations.collect(logs):
                    yield
            finally:
                try:
                    runtime.database_runtime.close()
                finally:
                    runtime.database.close()
    finally:
        lock.release()


async def safe_connection_validation(request: Request, exc: RequestValidationError):
    if request.url.path == '/api/v1/magic-box/callbacks/task-finished':
        event(logger, logging.WARNING, '魔盒完成通知参数校验失败', error_code='CALLBACK_VALIDATION')
    if request.url.path.startswith('/api/v1/operations'):
        return JSONResponse(status_code=422, content={'success': False, 'error': {'code': 'VALIDATION_ERROR', 'message': '运维请求参数无效'}})
    if request.url.path.startswith('/api/v1/configuration/llm'):
        return JSONResponse(status_code=422, content={'success': False, 'error': {'code': 'VALIDATION_ERROR', 'message': '模型配置无效，请检查接口地址、模型 ID 和密钥'}})
    if request.url.path.startswith('/api/v1/database-monitors'):
        return JSONResponse(status_code=422, content={'success': False, 'error': {'code': 'VALIDATION_ERROR', 'message': '请填写名称、有效的服务器 IP 地址和 1～65535 的整数端口'}})
    if request.url.path.startswith(('/api/v1/welding', '/api/v1/database/welding/', '/api/v1/database/sources')) or request.url.path == '/api/v1/database/configuration':
        return JSONResponse(status_code=422, content={'success': False, 'error': {'code': 'VALIDATION_ERROR', 'message': '请求参数无效，请检查日期、筛选或连接配置'}})
    return await request_validation_exception_handler(request, exc)

async def request_id_middleware(request: Request, call_next):
    # A client scope can restrict capabilities, but never grants access or replaces role checks.
    if request.url.path == '/api/v1/operations/browser' and request.method == 'POST':
        length = request.headers.get('content-length', '')
        if request.headers.get('transfer-encoding') or not length.isascii() or not length.isdigit() or len(length) > 3 or int(length) > 512:
            return JSONResponse(status_code=413, content={'detail': '前端错误摘要超过大小限制'})
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = (
        supplied_request_id
        if REQUEST_ID_PATTERN.fullmatch(supplied_request_id)
        else f"req_{uuid.uuid4().hex[:12]}"
    )
    request.state.request_id = request_id
    execution_id = sql_request_cancellations.activate(
        request_id, cancellation_session_scope(request.cookies.get(SESSION_COOKIE)),
    )
    request.state.execution_id = execution_id
    token = bind_request_id(execution_id)
    started = monotonic()
    sql_line = source_key = None
    try:
        line_id = None if request.url.path.startswith('/api/v1/operations') else request.headers.get('X-Production-Line')
        if line_id:
            context = await run_in_threadpool(runtime_state, request)
            if not supports_path(context, line_id, request.url.path, request.method):
                return JSONResponse(status_code=403, content={'success': False, 'error': {'code': 'LINE_CAPABILITY_DENIED', 'message': '当前产线不支持此功能'}})
        selected = line_id or request.query_params.get('device_id')
        if selected and request.url.path.startswith(('/api/v1/workshop', '/api/v1/leadership-report',
                '/api/v1/equipment-report', '/api/v1/energy-report', '/api/v1/analysis', '/api/v1/welding',
                '/api/v1/options', '/api/v1/projects', '/api/v1/ships', '/api/v1/reports', '/api/v1/designer',
                '/api/v1/database/preview', '/api/v1/database/sql/execute')):
            context = await run_in_threadpool(runtime_state, request)
            line = context.lines.get(selected)
            if line is not None:
                sql_line, source_key = line.config.id, line.config.source_key
        elif request.url.path.startswith(('/api/v1/cutting', '/api/v1/pdm-statistics')):
            settings = await run_in_threadpool(request_settings, request)
            source_key = (settings.dispatch_source_key if request.url.path.endswith(('/machines', '/status'))
                          else settings.file_source_key)
        with sql_identity(sql_line, source_key), operation_context(request_id=request_id, line_id=sql_line, source_id=source_key):
            response = await call_next(request)
            matched_route = request.scope.get('route')
            route = getattr(matched_route, 'name', None)
            if (route and request.url.path.startswith('/api/v1/')
                    and not request.url.path.startswith('/api/v1/operations') and request.url.path != '/api/v1/health'
                    and not getattr(request.state, 'operation_timeout_logged', False)
                    and 'X-SQL-Resource-Error' not in response.headers):
                status = response.status_code
                transient = status in (503, 504)
                operations = application_state(request).operations
                repeated = operations.repeated_http_failure(route, sql_line, source_key, status)
                if status >= 500:
                    code = {503: 'HTTP_UNAVAILABLE', 504: 'HTTP_TIMEOUT'}.get(status, 'HTTP_SERVER_ERROR')
                    feature = getattr(matched_route, 'path', '').removeprefix('/api/v1/').partition('/')[0]
                    event(logger, logging.WARNING if transient else logging.ERROR, 'HTTP 接口返回服务端错误',
                          error_code=code, resource_id=route, stage=feature, incident=repeated or not transient,
                          duration_ms=(monotonic() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception:
        with operation_context(request_id=request_id, line_id=sql_line, source_id=source_key):
            matched_route = request.scope.get('route')
            feature = getattr(matched_route, 'path', '').removeprefix('/api/v1/').partition('/')[0]
            event(logger, logging.ERROR, 'HTTP 请求发生未处理异常', error_code='INTERNAL_ERROR',
                  incident=True, exc_info=True, resource_id=getattr(matched_route, 'name', None), stage=feature,
                  duration_ms=(monotonic() - started) * 1000)
        raise
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution_id)


async def sql_capacity_error_handler(request: Request, exc: SqlCapacityError):
    return JSONResponse(status_code=503, headers={'Retry-After': '1', 'X-SQL-Resource-Error': exc.reason},
        content={'success': False, 'error': {'code': 'SQL_' + exc.reason.upper(), 'message': str(exc)},
                 'request_id': getattr(request.state, 'request_id', 'unknown')})


async def sql_request_cancelled_handler(request: Request, exc: SqlRequestCancelled):
    return JSONResponse(
        status_code=499,
        content={
            "success": False,
            "error": {"code": "REQUEST_CANCELLED", "message": str(exc)},
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


async def operation_timeout_handler(request: Request, exc: ReportOperationTimeout):
    request.state.operation_timeout_logged = True
    event(logger, logging.WARNING, '请求执行超过期限', error_code='OPERATION_TIMEOUT', incident=True,
          resource_id=getattr(request.scope.get('route'), 'name', None))
    return JSONResponse(status_code=504,
        content={'success': False, 'error': {'code': 'OPERATION_TIMEOUT', 'message': str(exc)},
                 'request_id': getattr(request.state, 'request_id', 'unknown')})


async def unexpected_error_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', 'unknown')
    return JSONResponse(
        status_code=500,
        headers={'X-Request-ID': request_id},
        content={
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": "系统处理失败，请联系管理员。"},
            "request_id": request_id,
        },
    )


async def health(request: Request) -> dict:
    settings = await to_thread.run_sync(request_settings, request, limiter=_health_threads)
    mode = settings.effective_data_source_mode
    reader = application_state(request).worker_status_reader
    worker_status = await to_thread.run_sync(reader.status, limiter=_health_threads)
    ready = background_ready(worker_status)
    web_ready = worker_status.get("database_available") is not False
    result = {
        "success": web_ready,
        "data": {
            "status": "ok" if web_ready else "degraded",
            "background_ready": ready,
            "background_status": worker_status.get("background_state", worker_status.get("state", "running" if ready else "degraded")),
            "workers": public_status(worker_status),
            "version": settings.app_version,
            "demo_mode": mode == "demo",
            "data_source_mode": mode,
            "data_source_name": (
                "SQL Server" if mode == "sqlserver" else "演示数据"
            ),
        },
        "request_id": request.state.request_id,
    }
    return result if web_ready else JSONResponse(status_code=503, content=result)


def frontend_favicon(request: Request):
    favicon_path = request_settings(request).frontend_dist_dir / "favicon.svg"
    if favicon_path.is_file():
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return HTMLResponse(status_code=404)


def frontend_index(request: Request):
    index_path = request_settings(request).frontend_dist_dir / "index.html"
    if index_path.is_file():
        return FileResponse(index_path, headers={'Cache-Control': 'no-store'})
    return HTMLResponse(
        """
        <!doctype html><html lang="zh-CN"><meta charset="utf-8">
        <title>切割生产智能产报系统</title>
        <body style="font-family:sans-serif;padding:40px">
        <h1>后端服务已启动</h1>
        <p>Vue 前端尚未构建，请进入 frontend 执行 npm install 与 npm run build。</p>
        <p><a href="/api/docs">打开 API 文档</a></p>
        </body></html>
        """
    )


def frontend_spa(frontend_path: str, request: Request):
    if frontend_path.startswith(("api/", "reports/", "assets/")):
        raise HTTPException(status_code=404, detail="Not Found")
    index_path = request_settings(request).frontend_dist_dir / "index.html"
    if index_path.is_file():
        return FileResponse(index_path, headers={'Cache-Control': 'no-store'})
    raise HTTPException(status_code=404, detail="Vue 前端尚未构建")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = Settings() if settings is None else settings
    app = FastAPI(
        title="切割生产智能产报系统 API",
        version=settings.app_version,
        lifespan=partial(lifespan, settings=settings),
        docs_url="/api/docs" if settings.app_env != "production" else None,
        redoc_url=None,
    )
    app.add_exception_handler(RequestValidationError, safe_connection_validation)
    app.add_exception_handler(SqlCapacityError, sql_capacity_error_handler)
    app.add_exception_handler(SqlRequestCancelled, sql_request_cancelled_handler)
    app.add_exception_handler(ReportOperationTimeout, operation_timeout_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type", "X-Request-ID", "X-SQL-Workbench-Token", "X-Production-Line"],
        )
    app.middleware("http")(request_id_middleware)

    app.include_router(devices_router)
    app.include_router(production_lines_router)
    app.include_router(pdm_statistics_router)
    app.include_router(welding_router)
    app.include_router(reports_router)
    app.include_router(auth_router)
    app.include_router(alarms_router)
    app.include_router(database_monitors_router)
    app.include_router(operations_router)
    app.include_router(llm_usage_router)
    app.include_router(options_router)
    app.include_router(database_router)
    app.include_router(configuration_router)
    app.include_router(designer_router)
    app.include_router(leadership_router)
    app.include_router(equipment_router)
    app.include_router(energy_router)
    app.include_router(workshop_router)
    app.include_router(analysis_router)
    app.include_router(automation_router)
    app.include_router(cutting_router)
    app.include_router(magic_box_callback_router)
    app.include_router(downloads_router)
    app.include_router(conditions_router)
    app.include_router(requests_router)
    app.include_router(view_router)

    app.add_api_route("/api/v1/health", health)
    assets_dir = settings.frontend_dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", FrontendAssets(directory=assets_dir), name="frontend-assets")
    app.add_api_route("/favicon.svg", frontend_favicon, include_in_schema=False)
    app.add_api_route("/", frontend_index, include_in_schema=False)
    app.add_api_route("/{frontend_path:path}", frontend_spa, include_in_schema=False)
    return app


@cache
def _process_app() -> FastAPI:
    return create_app()


def __getattr__(name: str):
    if name == 'app':
        return _process_app()
    raise AttributeError(name)
