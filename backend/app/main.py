from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

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
from app.config import DEVICE_CATALOG, settings, settings_for_device
from app.api.devices import router as devices_router
from app.services.device_runtime import build_device_runtime
from app.database.device_migrations import backup_before_device_upgrade
from app.database.metadata import MetadataRepository
from app.database.llm_usage import LlmUsageRepository
from app.database.leadership_snapshots import LeadershipSnapshotRepository
from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.database.retention import RetentionRepository
from app.database.workshop import WorkshopRepository
from app.database.sqlite import SQLiteDatabase
from app.services.retention_service import RetentionService
from app.services.request_cancellation import (
    SqlRequestCancelled,
    bind_request_id,
    cancellation_session_scope,
    reset_request_id,
    sql_request_cancellations,
)
from app.services.worker_auth_service import WorkerAuthService
from app.services.automation_service import AutomationService
from app.services.automation_runtime import AutomationRuntime
from app.services.cutting_dispatch_service import CuttingDispatchService
from app.services.email_service import EmailService
from app.services.file_source import build_file_source
from app.services.file_download_service import FileDownloadService
from app.services.magic_box_service import MagicBoxConfigProvider
from app.services.pdf_service import PdfService
from app.workers.report_worker import ReportWorker
from app.workers.retention_worker import RetentionWorker
from app.workers.download_cleanup_worker import DownloadCleanupWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_runtime_dirs()
    database = SQLiteDatabase(settings.metadata_db_path)
    backup_before_device_upgrade(database)
    database.initialize()
    metadata = MetadataRepository(database)
    metadata.initialize()
    llm_usage = LlmUsageRepository(database)
    llm_usage.initialize()
    app.state.llm_usage = llm_usage
    leadership_snapshots = LeadershipSnapshotRepository(database)
    leadership_snapshots.initialize()
    workshop = WorkshopRepository(database)
    workshop.initialize()
    automation = AutomationRepository(database)
    automation.initialize()
    definitions = DefinitionRepository(database, settings.timezone)
    definitions.initialize()
    dispatches = DispatchRepository(database)
    dispatches.initialize()
    pdf_service = PdfService(operation_timeout_seconds=settings.pdf_render_timeout_seconds)
    devices = {
        device.id: build_device_runtime(settings_for_device(settings, device.id), database,
                                        definitions, leadership_snapshots, pdf_service)
        for device in DEVICE_CATALOG.devices
    }
    app.state.devices = devices
    default = devices[DEVICE_CATALOG.default_device_id]
    automation_runtime = AutomationRuntime(settings)
    email_service = EmailService(settings, runtime=automation_runtime)
    automation_service = AutomationService(
        settings, automation, metadata, email_service, runtime=automation_runtime,
    )
    automation_service.device_services = devices
    worker = ReportWorker(
        settings, metadata, default.pipeline, automation_service=automation_service,
        runtime=automation_runtime, pipelines={key: value.pipeline for key, value in devices.items()},
    )
    app.state.metadata = metadata
    app.state.sqlite_database = database
    app.state.workshop = workshop  # Authentication and users are shared across devices.
    app.state.definitions = definitions
    app.state.automation = automation
    app.state.automation_runtime = automation_runtime
    app.state.automation_service = automation_service
    app.state.email_service = email_service
    app.state.worker_auth = WorkerAuthService(settings)
    for name in ('query_service', 'energy_service', 'energy_period_service', 'range_report_service',
                 'leadership_report_service', 'report_analysis_service', 'leadership_refresh_worker',
                 'current_report_refresh_worker', 'energy_history_worker', 'alarm_repository',
                 'alarm_monitor_service', 'alarm_worker'):
        setattr(app.state, name, getattr(default, name))
    pdm = (
        SqlServerPdmRepository(settings)
        if settings.file_database_enabled
        else MockPdmRepository()
    )
    app.state.dispatches = dispatches
    file_source = build_file_source(settings)
    downloads = DownloadRepository(database)
    downloads.initialize()
    file_downloads = FileDownloadService(settings, downloads, pdm, file_source)
    app.state.file_downloads = file_downloads
    downloads.recover_interrupted(file_downloads.now())
    download_cleanup_worker = DownloadCleanupWorker(settings, file_downloads)
    app.state.download_cleanup_worker = download_cleanup_worker
    retention_repository = RetentionRepository(database)
    retention_repository.initialize()
    retention_service = RetentionService(settings, retention_repository)
    retention_worker = RetentionWorker(settings, retention_service)
    app.state.retention_service = retention_service
    app.state.retention_worker = retention_worker
    magic_config = MagicBoxConfigProvider(settings)
    app.state.cutting_dispatch = CuttingDispatchService(
        settings,
        pdm,
        dispatches,
        file_source,
        file_downloads,
        magic_config,
    )
    dispatches.recover_interrupted(app.state.cutting_dispatch._now())
    app.state.worker = worker
    app.state.pdf_service = pdf_service
    background_workers = [worker, *(task for device in devices.values() for task in device.workers),
                          download_cleanup_worker, retention_worker]
    started_workers = []
    try:
        for task in background_workers:
            started_workers.append(task)
            task.start()
        yield
    finally:
        for task in reversed(started_workers):
            task.stop()


app = FastAPI(
    title="切割生产智能产报系统 API",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)

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
app.include_router(reports_router)
app.include_router(auth_router)
app.include_router(alarms_router)
app.include_router(llm_usage_router)
app.include_router(options_router)
app.include_router(database_router)
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
def health(request: Request) -> dict:
    mode = settings.effective_data_source_mode
    worker = getattr(request.app.state, "worker", None)
    worker_status = worker.status() if worker is not None else {"report_alive": False}
    ready = (
        worker_status["report_alive"]
        and worker_status.get("automation_alive") is not False
        and worker_status.get("email_alive") is not False
        and not worker_status.get("stalled_workers")
        and worker_status.get("queue", {}).get("available") is not False
    )
    result = {
        "success": ready,
        "data": {
            "status": "ok" if ready else "degraded",
            "workers": worker_status,
            "version": settings.app_version,
            "demo_mode": mode == "demo",
            "data_source_mode": mode,
            "data_source_name": (
                "SQL Server" if mode == "sqlserver" else "演示数据"
            ),
        },
        "request_id": request.state.request_id,
    }
    if not ready:
        return JSONResponse(status_code=503, content=result)
    return result


assets_dir = settings.frontend_dist_dir / "assets"
if assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")


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
        return FileResponse(index_path)
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
        return FileResponse(index_path)
    raise HTTPException(status_code=404, detail="Vue 前端尚未构建")
