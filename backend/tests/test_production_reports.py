from datetime import date
from types import SimpleNamespace

from app.config import Settings
from app.services import production_lines
from app.services.application_runtime import build_runtime
from app.services.leadership_report_service import LeadershipReportService
from app.services.production_reports import CuttingReportProvider, report_provider, report_providers
from app.services.report_snapshots import ReportSnapshotService
from app.database.report_snapshots import ReportKey
from app.worker import WorkerProcess


def test_cutting_provider_uses_public_source_identity_and_retains_month_and_detail_contract(tmp_path):
    service = LeadershipReportService(Settings(project_root=tmp_path, data_source_mode='demo'))
    provider = CuttingReportProvider(service)
    query = {'report_month': '2026-10-01'}
    result = provider.fetch_summary('leadership', query, requested_at=1)
    assert provider.source_identity() == service.source_identity()
    assert result.payload.report_month == date(2026, 10, 1)
    assert provider.fetch_detail('leadership', query) == result.payload.daily_records
    assert provider.refresh_plan() == []


def test_registered_third_line_uses_existing_worker_without_type_specific_dispatch(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path, data_source_mode='demo', weld_enabled=False)
    runtime = build_runtime(cfg, role='worker')
    key = ReportKey('assembly', 'overview', '2026-10-01', '2026-10-02', {'station': 2}, 'source-v1')
    payload = ReportSnapshotService.task_payload(key)
    refreshed = []
    provider = SimpleNamespace(
        report_types=('overview',), source_identity=lambda: 'source-v1',
        refresh_plan=lambda: [(payload, 30)],
        refresh_summary=lambda kind, query, **kwargs: refreshed.append((kind, query, kwargs)),
    )
    service = SimpleNamespace(settings=SimpleNamespace(enabled=True), report_provider=provider)
    context = SimpleNamespace(devices={}, assembly=service)
    monkeypatch.setattr(runtime.database_runtime, 'snapshot', lambda: context)
    monkeypatch.setattr(production_lines, 'SERVICE_LINES', (*production_lines.SERVICE_LINES, {
        'id': 'assembly', 'name': '组装线', 'type': 'assembly',
        'service_key': 'assembly', 'enabled_setting': 'enabled',
    }))
    monkeypatch.setitem(production_lines.LINE_TYPES, 'assembly', {
        'type_name': '组装', 'home_path': '/admin/assembly', 'capabilities': ['reports'],
        'api_modules': ('assembly',),
    })
    assert report_provider(context, 'assembly') is provider
    assert list(report_providers(context)) == [('assembly', provider)]
    process = WorkerProcess(cfg, runtime)
    try:
        process._schedule()
        process._schedule()
        with runtime.coordination.connect() as connection:
            tasks = connection.execute("SELECT * FROM background_tasks WHERE kind='report_snapshot_refresh'").fetchall()
        assert len(tasks) == 1 and tasks[0]['device_id'] == 'assembly'
        task = {'kind': 'report_snapshot_refresh', 'device_id': 'assembly', 'payload': payload, 'created_at': tasks[0]['created_at']}
        process._handle(task)
        assert refreshed == [('overview', payload['query'], {'requested_at': task['created_at']})]
        provider.source_identity = lambda: 'source-v2'
        process._handle(task)
        assert len(refreshed) == 1
        service.settings.enabled = False
        assert list(report_providers(context)) == []
    finally:
        process.stop()
