from tests.postgres_fixtures import state_settings
from datetime import date
from types import SimpleNamespace
from dataclasses import replace

from app.services.application_runtime import build_runtime
from app.services.leadership_report_service import LeadershipReportService
from app.services.production_reports import CuttingReportProvider, report_provider, report_providers
from app.services.report_snapshots import ReportSnapshotService
from app.database.report_snapshots import ReportKey
from app.worker import WorkerProcess
from tests.line_fixtures import configuration, cutting_settings


def test_cutting_provider_preserves_month_summary_detail_and_business_refresh_contract(tmp_path):
    service = LeadershipReportService(cutting_settings(project_root=tmp_path, data_source_mode='demo'))
    provider = CuttingReportProvider(service)
    query = {'report_month': '2026-10-01'}
    result = provider.fetch_summary('leadership', query, requested_at=1)
    assert provider.source_identity() == service.source_identity()
    assert result.payload.report_month == date(2026, 10, 1)
    assert provider.fetch_detail('leadership', query) == result.payload.daily_records
    assert provider.refresh_plan() == []


def test_worker_dispatches_instance_provider_without_a_global_welding_field(postgres_state, tmp_path):
    cfg = configuration(tmp_path, welding=True)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='worker')
    context = runtime.database_runtime.snapshot()
    key = ReportKey('welding', 'dashboard', '2026-10-01', '2026-10-02', {}, 'source-v1')
    payload = ReportSnapshotService.task_payload(key)
    refreshed = []
    provider = context.lines['welding'].report_provider
    provider.source_identity = lambda: 'source-v1'
    provider.refresh_plan = lambda: [(payload, 30)]
    provider.refresh_summary = lambda kind, query, **kwargs: refreshed.append((kind, query, kwargs))
    assert report_provider(context, 'welding') is provider
    process = WorkerProcess(cfg, runtime)
    try:
        process._schedule()
        process._schedule()
        with runtime.coordination.connect() as connection:
            tasks = connection.execute("SELECT * FROM background_tasks WHERE kind='report_snapshot_refresh'").fetchall()
        assert len(tasks) == 1 and tasks[0]['device_id'] == 'welding'
        task = {'kind': 'report_snapshot_refresh', 'device_id': 'welding', 'payload': payload, 'created_at': tasks[0]['created_at']}
        process._handle(task)
        assert refreshed == [('dashboard', payload['query'], {'requested_at': task['created_at']})]
        provider.source_identity = lambda: 'source-v2'
        process._handle(task)
        assert len(refreshed) == 1
    finally:
        process.stop()
