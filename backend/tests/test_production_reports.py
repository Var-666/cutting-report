from tests.postgres_fixtures import state_settings
from datetime import date, timedelta
from types import SimpleNamespace
from dataclasses import replace
import pytest

from app.services.application_runtime import build_runtime
from app.services.leadership_report_service import LeadershipReportService
from app.services.production_reports import CuttingReportProvider, report_provider, report_providers
from app.services.report_snapshots import ReportSnapshotService
from app.database.report_snapshots import ReportKey, ReportSnapshotRepository
from app.services.energy_api import EnergyApiError
from app.services.range_report_service import RangeReportService
from app.worker import WorkerProcess
from app.scheduler import SchedulerProcess
from tests.line_fixtures import configuration, cutting_settings
from tests.energy_fixtures import record
from tests.energy_period_fixtures import DAY, START, service


def test_cutting_provider_preserves_month_summary_detail_and_business_refresh_contract(tmp_path):
    service = LeadershipReportService(cutting_settings(project_root=tmp_path, data_source_mode='demo'))
    provider = CuttingReportProvider(service)
    query = {'report_month': '2026-10-01'}
    result = provider.fetch_summary('leadership', query, requested_at=1)
    assert provider.source_identity() == service.source_identity()
    assert result.payload.report_month == date(2026, 10, 1)
    assert provider.fetch_detail('leadership', query) == result.payload.daily_records
    assert provider.refresh_plan() == []


@pytest.mark.parametrize('report_type', ['energy:tasks', 'energy:range'])
@pytest.mark.parametrize('overlap_seconds,conflict', [(60, False), (61, True)])
def test_task_energy_snapshots_save_overlap_warnings_and_preserve_last_result_on_source_failure(
    report_type, overlap_seconds, conflict, tmp_path, state_database,
):
    energy, meter = service(tmp_path, state_database, project_root=tmp_path)
    start = START + timedelta(hours=8)
    rows = [record('valid', production_time=start, production_end_time=start + timedelta(minutes=30))]
    query_service = SimpleNamespace(fetch_equipment_data=lambda *args, **kwargs: SimpleNamespace(
        records=rows, data_updated_at=start,
    ))
    pipeline = SimpleNamespace(
        query_service=query_service,
        energy_period_service=energy,
        range_service=RangeReportService(energy.settings, query_service, None, energy),
    )
    provider = CuttingReportProvider(LeadershipReportService(energy.settings), pipeline=pipeline,
                                     snapshots=ReportSnapshotRepository(state_database))
    query = {'start': DAY, 'end': DAY}

    provider.refresh_summary(report_type, query)
    initial = provider.fetch_summary(report_type, query)
    boundary = start + timedelta(hours=2)
    rows.extend([
        record('overlap-1', production_time=start + timedelta(hours=1), production_end_time=boundary),
        record('overlap-2', production_time=boundary - timedelta(seconds=overlap_seconds),
               production_end_time=start + timedelta(hours=3)),
    ])
    provider.refresh_summary(report_type, query)
    updated = provider.fetch_summary(report_type, query)
    task_report = updated.payload.get('task_report', updated.payload)
    assert updated.meta['data_version'] != initial.meta['data_version']
    assert not updated.meta['refresh_failed']
    assert bool(task_report['warning']) == conflict
    expected_total = 18 if conflict else 90.6
    assert task_report['summary']['total_electricity_consumption'] == pytest.approx(expected_total)
    if report_type == 'energy:range':
        assert updated.payload['period_report']['summary']['electricity']['value'] == pytest.approx(expected_total)
    assert {row['task_id']: row['electricity_data_status'] for row in task_report['records']} == {
        'valid': 'measured',
        'overlap-1': 'overlap' if conflict else 'measured',
        'overlap-2': 'overlap' if conflict else 'interpolated',
    }

    meter.error = EnergyApiError('能管接口连接失败')
    with pytest.raises(ConnectionError, match='能耗取数失败'):
        provider.refresh_summary(report_type, query)
    retained = provider.fetch_summary(report_type, query)
    assert retained.meta['data_version'] == updated.meta['data_version']
    assert retained.payload == updated.payload


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
        scheduler = SchedulerProcess(cfg, runtime)
        scheduler._schedule()
        scheduler._schedule()
        with runtime.coordination.connect() as connection:
            tasks = connection.execute("SELECT * FROM background_tasks WHERE kind='report_snapshot_refresh'").fetchall()
        assert len(tasks) == 1 and tasks[0]['device_id'] == 'welding'
        task = {'kind': 'report_snapshot_refresh', 'device_id': 'welding', 'payload': payload, 'created_at': tasks[0]['created_at']}
        process._handle(task)
        assert refreshed == [('dashboard', payload['query'], {'requested_at': task['created_at']})]
        provider.source_identity = lambda: 'source-v2'
        with pytest.raises(RuntimeError, match='来源'):
            process._handle(task)
        assert len(refreshed) == 1
    finally:
        process.stop()
