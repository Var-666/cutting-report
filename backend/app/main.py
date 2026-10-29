from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

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
from app.api.cutting import router as cutting_router
from app.api.downloads import router as downloads_router
from app.api.conditions import router as conditions_router
from app.api.auth import SESSION_COOKIE, router as auth_router
from app.api.alarms import router as alarms_router
from app.api.llm_usage import router as llm_usage_router
from app.api.designer import router as designer_router
from app.api.equipment import router as equipment_router
from app.api.energy import router as energy_router
from app.api.leadership import router as leadership_router
from app.api.options import router as options_router
from app.api.reports import router as reports_router
from app.api.reports import view_router
from app.api.requests import REQUEST_ID_PATTERN, router as requests_router
from app.api.workshop import router as workshop_router
from app.config import settings
from app.api.devices import router as devices_router
from app.api.runtime import runtime_state
from app.api.welding import router as welding_router
from app.api.production_lines import router as production_lines_router
from app.services.application_runtime import build_runtime
from app.services.process_lock import FileLock
from app.services.worker_status import background_ready
from app.services.request_cancellation import (
    SqlRequestCancelled, bind_request_id, cancellation_session_scope,
    reset_request_id, sql_request_cancellations,
)
from anyio import CapacityLimiter, to_thread

_health_threads = CapacityLimiter(2)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dispatch recovery is safe only after the previous Web process has exited.
    lock = FileLock(settings.metadata_db_path.parent / ".web-process.lock")
    if not lock.acquire(blocking=False):
        raise RuntimeError("已有 Web 进程运行；请保持一个 Web 和一个独立 Worker")
    try:
        runtime = build_runtime(settings, role="web")
        for name, value in vars(runtime).items():
            setattr(app.state, name, value)
        yield
    finally:
        lock.release()


app = FastAPI(
    title="切割生产智能产报系统 API",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)


@app.exception_handler(RequestValidationError)
async def safe_connection_validation(request: Request, exc: RequestValidationError):
    if request.url.path.startswith(('/api/v1/welding', '/api/v1/database/welding/')) or request.url.path == '/api/v1/database/configuration':
        return JSONResponse(status_code=422, content={'success': False, 'error': {'code': 'VALIDATION_ERROR', 'message': '请求参数无效，请检查日期、筛选或连接配置'}})
    return await request_validation_exception_handler(request, exc)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-Request-ID", "X-SQL-Workbench-Token"],
    )


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    # A client scope can restrict capabilities, but never grants access or replaces role checks.
    line_id = request.headers.get('X-Production-Line')
    if line_id and not supports_path(line_id, request.url.path):
        return JSONResponse(status_code=403, content={'success': False, 'error': {'code': 'LINE_CAPABILITY_DENIED', 'message': '当前产线不支持此功能'}})
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
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution_id)


@app.exception_handler(SqlRequestCancelled)
async def sql_request_cancelled_handler(request: Request, exc: SqlRequestCancelled):
    return JSONResponse(
        status_code=499,
        content={
            "success": False,
            "error": {"code": "REQUEST_CANCELLED", "message": str(exc)},
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    logging.getLogger(__name__).exception("unhandled request error")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": "系统处理失败，请联系管理员。"},
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )


app.include_router(devices_router)
app.include_router(production_lines_router)
app.include_router(welding_router)
app.include_router(reports_router)
app.include_router(auth_router)
app.include_router(alarms_router)
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
app.include_router(downloads_router)
app.include_router(conditions_router)
app.include_router(requests_router)
app.include_router(view_router)


@app.get("/api/v1/health")
async def health(request: Request) -> dict:
    configuration = await to_thread.run_sync(runtime_state, request, limiter=_health_threads)
    mode = getattr(configuration, "settings", settings).effective_data_source_mode
    reader = getattr(request.app.state, "worker_status_reader", None)
    worker_status = await to_thread.run_sync(reader.status, limiter=_health_threads) if reader else {"state": "missing", "report_alive": False}
    ready = background_ready(worker_status)
    web_ready = worker_status.get("database_available") is not False
    result = {
        "success": web_ready,
        "data": {
            "status": "ok" if web_ready else "degraded",
            "background_ready": ready,
            "background_status": worker_status.get("state", "running" if ready else "degraded"),
            "workers": {key: value for key, value in worker_status.items() if key != "configuration"},
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


assets_dir = settings.frontend_dist_dir / "assets"
if assets_dir.is_dir():
    app.mount("/assets", FrontendAssets(directory=assets_dir), name="frontend-assets")


@app.get("/favicon.svg", include_in_schema=False)
def frontend_favicon():
    favicon_path = settings.frontend_dist_dir / "favicon.svg"
    if favicon_path.is_file():
        return FileResponse(favicon_path, media_type="image/svg+xml")
    return HTMLResponse(status_code=404)


@app.get("/", include_in_schema=False)
def frontend_index():
    index_path = settings.frontend_dist_dir / "index.html"
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


@app.get("/{frontend_path:path}", include_in_schema=False)
def frontend_spa(frontend_path: str):
    if frontend_path.startswith(("api/", "reports/", "assets/")):
        raise HTTPException(status_code=404, detail="Not Found")
    index_path = settings.frontend_dist_dir / "index.html"
    if index_path.is_file():
        return FileResponse(index_path, headers={'Cache-Control': 'no-store'})
    raise HTTPException(status_code=404, detail="Vue 前端尚未构建")
