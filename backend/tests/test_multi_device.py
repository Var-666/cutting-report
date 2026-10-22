from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlsplit
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.auth import require_admin, require_user
from app.api.devices import router as devices_router
from app.api.options import router as options_router
from app.api.reports import router as reports_router
from app.config import Settings, settings_for_device
from app.database.alarms import AlarmRepository
from app.database.alarm_source import SqlServerAlarmSource
from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.device_migrations import backup_before_device_upgrade, rebuild_table
from app.database.leadership_snapshots import LeadershipMonthSnapshot, LeadershipSnapshotRepository
from app.database.metadata import MetadataRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import ConfirmationConflictError, ConfirmationNotFoundError, WorkshopRepository
from app.models.domain import LeadershipPeriodRecord, WorkshopRecord
from app.services.automation_service import AutomationService
from app.services.device_runtime import build_device_runtime
from app.services.custom_report_service import CustomReportService
from app.services.pipeline import ReportPipeline
from app.services.energy_period_service import EnergyPeriodService
from app.services.energy_service import EquipmentEnergyService
from app.database.energy import EnergyReportRepository
from app.workers.report_worker import ReportWorker
from test_energy_service import MeterStub, record

NOW = datetime(2026, 10, 12, 12, tzinfo=ZoneInfo('Asia/Shanghai'))
STAMP = NOW.isoformat()
DAY = '2026-10-12'


def workshop_record(task_id):
    return WorkshopRecord(task_id, 'P1', 'S1', None, 'PROGRAM', Decimal('10'), Decimal('0'), Decimal('60'))


@pytest.fixture
def database(tmp_path):
    db = SQLiteDatabase(tmp_path / 'metadata.db')
    MetadataRepository(db).initialize()
    users = WorkshopRepository(db)
    users.initialize()
    admin = users.create_user(username='admin', display_name='管理员', password='admin-password', role='admin', now=STAMP)
    return db, admin


def test_cutter02_uses_shared_connection_but_cannot_inherit_any_cutter01_meter(tmp_path):
    base = Settings(project_root=tmp_path, data_source_mode='sqlserver', demo_mode=False, energy_api_enabled=True)
    cfg = settings_for_device(base, 'Cutter02')
    assert cfg.sqlserver_connection('production') == base.sqlserver_connection('production')
    assert (cfg.device_id, cfg.device_name, cfg.report_machine_no) == ('Cutter02', '30KW切割机', 'Cutter02')
    assert cfg.alarm_monitor_start_at == '2026-10-12 00:00:00'
    assert cfg.alarm_monitor_machine_nos == ('Cutter02',)
    assert not cfg.energy_connected and not cfg.energy_api_enabled
    assert cfg.energy_gas_parameter_id == ''
    assert cfg.energy_report_electricity_parameter_ids == cfg.energy_equipment_ids == cfg.energy_electricity_parameter_ids == ()
    assert cfg.energy_report_equipment_ids == ('Cutter02',)
    assert base.energy_report_equipment_ids == ('Cutter01',)
    with pytest.raises(ValueError, match='设备不存在'):
        settings_for_device(base, 'missing')


def test_two_devices_can_keep_same_day_snapshots_confirmations_and_corrections_separate(database):
    db, admin = database
    one = WorkshopRepository(db, scope_machine_no='Cutter01')
    two = WorkshopRepository(db, scope_machine_no='Cutter02')
    saved = []
    for repository, task in [(one, 'task-1'), (two, 'task-2')]:
        report = repository.sync_daily_report(report_date=DAY, source_mode='sqlserver', records=[workshop_record(task)],
                                              user=admin, now=STAMP, scope_machine_no=repository.scope_machine_no)
        repository.confirm_task(task_id=task, user=admin, report_id=report['report_id'], now=STAMP)
        saved.append(report)
    assert saved[0]['report_id'] != saved[1]['report_id']
    assert [row.task_id for row in one.get_daily_report_records(DAY)[1]] == ['task-1']
    assert [row.task_id for row in two.get_daily_report_records(DAY)[1]] == ['task-2']
    assert two.get_confirmations(['task-1', 'task-2']).keys() == {'task-2'}
    assert [row['task_id'] for row in two.list_confirmation_audits()] == ['task-2']
    with pytest.raises(ConfirmationNotFoundError):
        one.correct_confirmation(task_id='task-2', admin=admin, reason='不能跨设备更正', now=STAMP)
    with pytest.raises(ConfirmationNotFoundError):
        one.revoke_confirmation(task_id='task-2', user=admin, reason='不能跨设备撤销', now=STAMP)
    with pytest.raises(ConfirmationConflictError, match='其他设备'):
        one.confirm_task(task_id='task-2', user=admin, now=STAMP)
    two.revoke_confirmation(task_id='task-2', user=admin, reason='本人撤销', now=STAMP)
    assert one.get_confirmations(['task-1'])['task-1']['worker_id'] == admin['user_id']


def test_upgrade_preserves_legacy_snapshot_children_and_confirmations(database):
    db, admin = database
    legacy = WorkshopRepository(db)
    report = legacy.create_daily_report(report_date=DAY, source_mode='sqlserver', records=[workshop_record('old')],
                                        user=admin, now=STAMP, scope_machine_no=None)
    legacy.confirm_task(task_id='old', report_id=report['report_id'], user=admin, now=STAMP)
    # Restore the previous release's date-only unique constraint, then upgrade it.
    with db.connect() as connection:
        connection.execute('DROP INDEX idx_workshop_reports_device_date')
    rebuild_table(db, 'workshop_reports', lambda sql: sql.replace('report_date TEXT NOT NULL', 'report_date TEXT NOT NULL UNIQUE'))
    legacy.initialize()
    one = WorkshopRepository(db, scope_machine_no='Cutter01')
    two = WorkshopRepository(db, scope_machine_no='Cutter02')
    assert one.get_daily_report(DAY)['report_id'] == report['report_id']
    assert one.get_daily_report_records(DAY)[1][0].confirmed_by_id == admin['user_id']
    two.sync_daily_report(report_date=DAY, source_mode='sqlserver', records=[workshop_record('new')],
                          user=admin, now=STAMP, scope_machine_no='Cutter02')
    legacy.initialize()  # Repeated startup must be safe and keep both reports.
    with db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM workshop_report_items').fetchone()[0] == 2
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_pre_upgrade_backup_is_readable_and_not_repeated_after_upgrade(tmp_path):
    db = SQLiteDatabase(tmp_path / 'metadata.db')
    with db.connect() as connection:
        connection.execute('CREATE TABLE report_jobs(report_id TEXT PRIMARY KEY)')
        connection.execute("INSERT INTO report_jobs VALUES ('historical-job')")
    backup_before_device_upgrade(db)
    copies = list((tmp_path / 'backups').glob('*.db'))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as connection:
        assert connection.execute('SELECT report_id FROM report_jobs').fetchone()[0] == 'historical-job'
    with db.connect() as connection:
        connection.execute("ALTER TABLE report_jobs ADD COLUMN device_id TEXT DEFAULT 'Cutter01'")
    backup_before_device_upgrade(db)
    assert list((tmp_path / 'backups').glob('*.db')) == copies


def test_analysis_versions_and_report_history_do_not_overwrite_other_device(database):
    db, _ = database
    one, two = (MetadataRepository(db, device_id=key) for key in ('Cutter01', 'Cutter02'))
    for repo, version in [(one, 'one'), (two, 'two')]:
        repo.save_analysis_source_version(report_type='equipment', report_date=DAY, data_version=version, updated_at=STAMP)
        repo.create_job(report_id=version, report_date=DAY, project_code=None, ship_no=None, request_id=version, created_at=STAMP)
    assert one.get_analysis_source_version(report_type='equipment', report_date=DAY)['data_version'] == 'one'
    assert two.get_analysis_source_version(report_type='equipment', report_date=DAY)['data_version'] == 'two'
    assert one.get_job('two') is None and two.get_job('one') is None
    assert [job['report_id'] for job in two.list_reports(page=1, page_size=10)[0]] == ['two']
    assert MetadataRepository(db).claim_next_job(STAMP)['device_id'] in {'Cutter01', 'Cutter02'}


def test_month_snapshots_with_same_month_are_kept_for_both_devices(database):
    db, _ = database
    repo = LeadershipSnapshotRepository(db)
    repo.initialize()
    month = date(2026, 10, 1)
    def snapshot(source, count):
        total = LeadershipPeriodRecord(period_start=month, task_count=count, cut_length_m=Decimal('1'),
                                       groove_length_m=Decimal('0'), work_time_seconds=60)
        return LeadershipMonthSnapshot(month, [total], [], total, NOW, NOW, NOW + timedelta(days=1), 0, source, 1)
    repo.upsert_many([snapshot('Cutter01-fingerprint', 1), snapshot('Cutter02-fingerprint', 2)])
    repo.initialize()
    assert repo.get(month, source_fingerprint='Cutter01-fingerprint', snapshot_version=1).total.task_count == 1
    assert repo.get(month, source_fingerprint='Cutter02-fingerprint', snapshot_version=1).total.task_count == 2


def alarm_event(key, machine):
    return {'alarm_info_id': key, 'machine_no': machine, 'alarm_type': 'CUTTER', 'alarm_code': str(key),
            'alarm_class': '故障', 'alarm_desc': '设备故障', 'start_time': '2026-10-12 08:00:00', 'remove_time': None}


def test_alarm_cursors_leases_recovery_and_page_totals_are_device_scoped(database):
    db, _ = database
    one = AlarmRepository(db, machine_no='Cutter01')
    two = AlarmRepository(db, machine_no='Cutter02', source_name='production:Cutter02')
    for repo, key, owner in [(one, 100, 'owner-1'), (two, 200, 'owner-2')]:
        repo.initialize()
        assert repo.try_acquire_lease(owner, NOW)
        repo.configure_monitoring_window('2026-10-12 00:00:00', lease_owner=owner, lease_now=NOW)
        repo.ingest([alarm_event(key, repo.machine_no), alarm_event(999, 'Other')], now=NOW,
                    cursor=key, debounce_minutes=10, lease_owner=owner, lease_now=NOW)
        incident = repo.list_unreported_incidents()[0]
        repo.reserve_report(proposed_report_id=owner, emission_key=owner, machine_no=repo.machine_no,
                            incident_ids=[incident['incident_id']], now=STAMP, lease_owner=owner, lease_now=NOW)
    assert one.get_cursor() == 100 and two.get_cursor() == 200
    assert one.list_open_source_ids() == [100] and two.list_open_source_ids() == [200]
    assert two.get_report('owner-1') is None
    assert len(two.list_reports()) == two.page_reports(monitor_start_at=DAY, page=1, page_size=10)['total'] == 1
    page = two.page_incidents(monitor_start_at=DAY, status=None, severity=None, page=1, page_size=10)
    assert page['all_total'] == page['open_count'] == page['severity_counts']['high'] == 1
    assert page['items'][0]['machine_no'] == 'Cutter02'
    assert two.recover_interrupted_reports('owner-2', NOW) == 0
    assert one.get_report('owner-1')['status'] == 'generating'
    one.release_lease('owner-1')
    assert one.try_acquire_lease('recovery', NOW)
    assert one.recover_interrupted_reports('recovery', NOW) == 1
    assert two.get_report('owner-2')['status'] == 'generating'


def test_alarm_source_always_filters_cutter02_and_its_confirmed_start_time():
    calls = []
    client = SimpleNamespace(fetch_all=lambda sql, params: calls.append((sql, params)) or [])
    source = SqlServerAlarmSource(settings_for_device(Settings(), 'Cutter02'), client)
    source.fetch_new(0, 1000, 500)
    sql, params = calls[0]
    assert 'START_TIME' in sql and 'MACHINE_NO' in sql
    assert params == (0, 1000, '2026-10-12 00:00:00', 'Cutter02')


def test_disconnected_energy_retains_cutter02_tasks_and_never_calls_meters(tmp_path):
    cfg = settings_for_device(Settings(project_root=tmp_path, energy_api_enabled=True), 'Cutter02')
    repo = EnergyReportRepository(SQLiteDatabase(tmp_path / 'energy.db'))
    repo.initialize()
    client = MeterStub()
    service = EnergyPeriodService(cfg, repo, client=client, clock=lambda: NOW)
    task = record('cutter02-task', equipment_id='Cutter02')
    report = service.get_task_day(NOW.date(), [task, record('wrong-device')])
    assert [row['task_id'] for row in report['records']] == ['cutter02-task']
    assert report['summary']['task_count'] == 1
    assert report['records'][0]['gas_consumption'] is None
    assert report['summary']['total_electricity_consumption'] is None
    assert report['configuration_error'] == '暂未接入' and report['energy_connected'] is False
    assert service.get_day(NOW.date())['summary']['gas']['value'] is None
    legacy = EquipmentEnergyService(replace(cfg, data_source_mode='demo'), client=client).enrich([task])
    assert legacy.records[0].gas_consumption is None
    assert not client.calls


def test_custom_sql_cannot_silently_use_cutter01_scope_or_override_device_parameter():
    service = CustomReportService(settings_for_device(Settings(), 'Cutter02'))
    arguments = dict(report_date=NOW.date(), period_type='day', project_code=None,
                     ship_no=None, parameters={'machine_no': 'Cutter01'})
    with pytest.raises(ValueError, match=':machine_no'):
        service._compile('SELECT TASK_ID FROM dbo.task', **arguments)
    sql, values = service._compile('SELECT TASK_ID FROM dbo.task WHERE MACHINE_NO = :machine_no', **arguments)
    assert 'MACHINE_NO = ?' in sql and values == ('Cutter02',)


def test_cutter02_export_explains_disconnected_energy_even_without_tasks(tmp_path):
    cfg = settings_for_device(Settings(project_root=tmp_path, data_source_mode='demo',
                                       demo_mode=True, llm_enabled=False), 'Cutter02')
    cfg.ensure_runtime_dirs()
    cfg.echarts_vendor_path.parent.mkdir(exist_ok=True)
    cfg.echarts_vendor_path.write_text('window.echarts = {};')
    metadata = MetadataRepository(cfg.metadata_db_path, device_id='Cutter02')
    metadata.initialize()
    pipeline = ReportPipeline(cfg, metadata)
    metadata.create_job(report_id='cutter02-export', report_date=DAY, project_code=None, ship_no=None,
                        request_id='export', created_at=STAMP, report_type='equipment')
    pipeline.run(metadata.claim_next_job(STAMP))
    completed = metadata.get_job('cutter02-export')
    assert completed['status'] == 'completed'
    html = (cfg.reports_dir / completed['relative_path']).read_text()
    assert '30KW切割机' in html and '能耗暂未接入' in html
    assert '30KW切割机' in completed['filename']


def test_schedule_run_and_queue_keep_fixed_device_even_after_schedule_edit(database):
    db, admin = database
    automation = AutomationRepository(db)
    automation.initialize()
    data = {'name': 'Cutter02 日报', 'device_id': 'Cutter02', 'report_type': 'equipment', 'frequency': 'daily',
            'run_time': '18:00', 'report_period': 'previous_day', 'recipients': ['manager@example.test'],
            'subject_template': '{report_name}', 'chart_type': 'bar_line', 'attach_report': True,
            'max_attempts': 3, 'enabled': True, 'next_run_at': STAMP}
    schedule = automation.create_schedule(data, user=admin, now=STAMP)
    run = automation.reserve_run(schedule, scheduled_for=STAMP, report_date=DAY, trigger_type='manual', now=STAMP)
    automation.update_schedule(schedule['schedule_id'], {**data, 'device_id': 'Cutter01'}, now=STAMP)
    metadata = MetadataRepository(db)
    service = AutomationService(Settings(), automation, metadata, SimpleNamespace())
    assert service._create_report_job(run, NOW)
    job = metadata.get_job(automation.get_run(run['run_id'])['report_id'])
    assert job['device_id'] == 'Cutter02'
    executed = []
    pipelines = {key: SimpleNamespace(run=lambda job, key=key: executed.append(key)) for key in ('Cutter01', 'Cutter02')}
    worker = ReportWorker(Settings(), metadata, pipelines['Cutter01'], pipelines=pipelines)
    worker._process_report(job, ZoneInfo('Asia/Shanghai'))
    assert executed == ['Cutter02']


@pytest.mark.parametrize('default_device_id', ['Cutter01', 'Cutter02'])
@pytest.mark.parametrize('device_id', ['Cutter01', 'Cutter02'])
def test_device_api_validates_selection_and_scopes_history_and_exports(database, monkeypatch, default_device_id, device_id):
    from app.api import devices as devices_api, reports as reports_api, alarms as alarms_api
    configured_catalog = devices_api.DEVICE_CATALOG.model_copy(update={'default_device_id': default_device_id})
    for module in (devices_api, reports_api, alarms_api):
        monkeypatch.setattr(module, 'DEVICE_CATALOG', configured_catalog)
    db, admin = database
    app = FastAPI()
    for router in (devices_router, options_router, reports_router):
        app.include_router(router)
    app.dependency_overrides[require_user] = lambda: admin
    app.dependency_overrides[require_admin] = lambda: admin
    app.state.devices = {
        key: SimpleNamespace(settings=settings_for_device(Settings(), key), metadata=MetadataRepository(db, device_id=key),
                             query_service=SimpleNamespace(list_projects=lambda filters, key=key: [key]))
        for key in ('Cutter01', 'Cutter02')
    }
    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'multi-device'
        return await call_next(request)
    with TestClient(app) as client:
        catalog = client.get('/api/v1/devices').json()['data']
        assert catalog['default_device_id'] == default_device_id
        assert [(item['id'], item['energy_connected']) for item in catalog['items']] == [('Cutter01', True), ('Cutter02', False)]
        assert 'password' not in str(catalog).lower()
        assert client.get(f'/api/v1/options/projects?report_date={DAY}&device_id=Cutter02').json()['data']['items'] == ['Cutter02']
        assert client.get(f'/api/v1/options/projects?report_date={DAY}&device_id=missing').status_code == 422
        created = client.post(f'/api/v1/reports?device_id={device_id}', json={'report_type': 'equipment', 'report_date': DAY})
        assert created.status_code == 202
        report_id = created.json()['data']['report_id']
        assert client.get(created.json()['data']['status_url']).json()['data']['device_id'] == device_id
        other = 'Cutter02' if device_id == 'Cutter01' else 'Cutter01'
        assert client.get(f'/api/v1/reports/{report_id}/status?device_id={other}').status_code == 404
        assert client.get(f'/api/v1/reports?device_id={other}').json()['data']['total'] == 0
        assert client.get(f'/api/v1/reports?device_id={device_id}').json()['data']['total'] == 1
        job = {**app.state.devices[device_id].metadata.get_job(report_id), 'status': 'completed'}
        serialized = reports_api._serialize_job(job)
        alarm_url = alarms_api._device_url(SimpleNamespace(app=app, query_params={'device_id': device_id}), '/api/v1/alarms/reports/a1/view')
        for url in (serialized['view_url'], serialized['download_url'], alarm_url):
            resolved_device = parse_qs(urlsplit(url).query).get('device_id', [default_device_id])[0]
            assert resolved_device == device_id, 'report links must resolve to their owner under either default'


def test_device_runtime_has_independent_caches_and_disables_only_cutter02_energy_worker(database, tmp_path):
    db, _ = database
    definitions = DefinitionRepository(db, 'Asia/Shanghai')
    definitions.initialize()
    snapshots = LeadershipSnapshotRepository(db)
    snapshots.initialize()
    base = Settings(project_root=tmp_path, data_source_mode='sqlserver', demo_mode=False,
                    energy_api_enabled=True, alarm_monitor_enabled=True)
    one, two = [build_device_runtime(settings_for_device(base, key), db, definitions, snapshots, SimpleNamespace())
                for key in ('Cutter01', 'Cutter02')]
    assert one.query_service is not two.query_service
    assert one.report_analysis_service is not two.report_analysis_service
    for runtime in (one, two):
        # A page and an export for the same device must share in-flight analysis.
        assert runtime.report_analysis_service is runtime.pipeline.analysis_service
        assert runtime.current_report_refresh_worker.analysis_service is runtime.report_analysis_service
        assert runtime.leadership_refresh_worker.analysis_service is runtime.report_analysis_service
    assert one.workshop.database is two.workshop.database
    assert one.alarm_worker.enabled and two.alarm_worker.enabled
    assert one.energy_history_worker.enabled and not two.energy_history_worker.enabled
    assert two.current_report_refresh_worker.settings.report_machine_no == 'Cutter02'
    assert two.pipeline.settings.device_id == 'Cutter02'
