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
from app.api.auth import router as auth_router
from app.api.alarms import router as alarms_router
from app.api.designer import router as designer_router
from app.api.equipment import router as equipment_router
from app.api.energy import router as energy_router
from app.api.leadership import router as leadership_router
from app.api.options import router as options_router
from app.api.reports import router as reports_router
from app.api.reports import view_router
from app.api.workshop import router as workshop_router
from app.config import settings
from app.database.metadata import MetadataRepository
from app.database.alarms import AlarmRepository
from app.database.alarm_source import SqlServerAlarmSource
from app.database.leadership_snapshots import LeadershipSnapshotRepository
from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.database.workshop import WorkshopRepository
from app.database.sqlite import SQLiteDatabase
from app.services.pipeline import ReportPipeline
from app.services.leadership_report_service import LeadershipReportService
from app.services.report_analysis_service import ReportAnalysisService
from app.services.worker_auth_service import WorkerAuthService
from app.services.automation_service import AutomationService
from app.services.cutting_dispatch_service import CuttingDispatchService
from app.services.email_service import EmailService
from app.services.file_source import build_file_source
from app.services.file_download_service import FileDownloadService
from app.services.magic_box_service import MagicBoxConfigProvider
from app.services.pdf_service import PdfService
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.alarm_monitor_service import AlarmMonitorService
from app.services.alarm_report_service import AlarmReportService
from app.workers.report_worker import ReportWorker
from app.workers.alarm_worker import AlarmWorker
from app.workers.leadership_refresh_worker import LeadershipRefreshWorker
from app.workers.energy_history_worker import EnergyHistoryWorker
from app.workers.download_cleanup_worker import DownloadCleanupWorker


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_runtime_dirs()
    database = SQLiteDatabase(settings.metadata_db_path)
    database.initialize()
    metadata = MetadataRepository(database)
    metadata.initialize()
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
    pipeline = ReportPipeline(settings, metadata, definitions)
    pdf_service = PdfService()
    email_service = EmailService(settings)
    automation_service = AutomationService(
        settings, automation, metadata, email_service
    )
    worker = ReportWorker(
        settings, metadata, pipeline, automation_service=automation_service
    )
    app.state.metadata = metadata
    app.state.sqlite_database = database
    app.state.workshop = workshop
    app.state.definitions = definitions
    app.state.automation = automation
    app.state.automation_service = automation_service
    app.state.email_service = email_service
    app.state.query_service = pipeline.query_service
    # Page, analysis and export share the same auth/readings cache.
    app.state.energy_service = pipeline.energy_service
    app.state.energy_period_service = pipeline.energy_period_service
    app.state.range_report_service = pipeline.range_service
    app.state.worker_auth = WorkerAuthService(settings)
    leadership_report_service = LeadershipReportService(settings, leadership_snapshots)
    leadership_refresh_worker = LeadershipRefreshWorker(
        settings, leadership_report_service
    )
    energy_history_worker = EnergyHistoryWorker(
        settings, pipeline.energy_period_service
    )
    app.state.leadership_report_service = leadership_report_service
    app.state.leadership_refresh_worker = leadership_refresh_worker
    app.state.energy_history_worker = energy_history_worker
    app.state.report_analysis_service = ReportAnalysisService(settings, metadata)
    alarm_repository = AlarmRepository(database)
    alarm_repository.initialize()
    alarm_monitor_service = AlarmMonitorService(
        settings,
        alarm_repository,
        SqlServerAlarmSource(settings),
        AlarmAnalysisService(settings),
        AlarmReportService(settings, pdf_service),
    )
    alarm_worker = AlarmWorker(settings, alarm_monitor_service)
    app.state.alarm_repository = alarm_repository
    app.state.alarm_monitor_service = alarm_monitor_service
    app.state.alarm_worker = alarm_worker
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
    worker.start()
    leadership_refresh_worker.start()
    energy_history_worker.start()
    alarm_worker.start()
    download_cleanup_worker.start()
    try:
        yield
    finally:
        energy_history_worker.stop()
        download_cleanup_worker.stop()
        alarm_worker.stop()
        leadership_refresh_worker.stop()
        worker.stop()


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
    request.state.request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


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


app.include_router(reports_router)
app.include_router(auth_router)
app.include_router(alarms_router)
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
app.include_router(view_router)


@app.get("/api/v1/health")
def health(request: Request) -> dict:
    mode = settings.effective_data_source_mode
    return {
        "success": True,
        "data": {
            "status": "ok",
            "version": settings.app_version,
            "demo_mode": mode == "demo",
            "data_source_mode": mode,
            "data_source_name": (
                settings.task_excel_path.name if mode == "excel" else "SQL Server" if mode == "sqlserver" else "演示数据"
            ),
        },
        "request_id": request.state.request_id,
    }


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
