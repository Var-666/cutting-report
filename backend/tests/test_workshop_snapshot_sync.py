from tests.line_fixtures import as_line_services, runtime_line
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from app.database.workshop import (
    ConfirmationConflictError,
    DailyReportScopeMismatchError,
    DailyReportSourceMismatchError,
    WorkshopRepository,
)
from app.models.domain import WorkshopRecord


def record(task_id, *, project='P', work=60):
    return WorkshopRecord(task_id=task_id, project_code=project, ship_no=None,
                          segment_name='S', program_no='OP',
                          cut_length=Decimal('10'), groove_length=Decimal('1'),
                          work_time=Decimal(str(work)), production_time=datetime(2026, 9, 30, 8),
                          remark=None,
                          production_date=date(2026, 9, 30))


def setup(state_database, tmp_path):
    repository = WorkshopRepository(state_database, scope_machine_no='Cutter01')
    admin = repository.create_user(username='admin', display_name='管理员', password='password', role='admin', now='2026-10-01T00:00:00')
    worker = repository.create_user(username='w', display_name='工人', password='password', role='worker', now='2026-10-01T00:00:00')
    repository.test_admin_id, repository.test_worker_id = admin['user_id'], worker['user_id']
    return repository


def test_source_correction_routes_are_removed_without_removing_confirmation_routes():
    from app.api.workshop import router

    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()['paths']
    assert '/api/v1/workshop/tasks/confirm' in paths
    assert '/api/v1/workshop/tasks/{task_id}/revoke' in paths
    assert '/api/v1/workshop/tasks/{task_id}/correct' in paths
    with TestClient(app) as client:
        for path in ('/reports/source-corrections', '/reports/source-corrections/preview'):
            assert client.post(f'/api/v1/workshop{path}', json={}).status_code == 404


def test_repositories_preserve_confirmation_audits_without_reenabling_source_corrections(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    with repository.connect() as connection:
        permissions = connection.execute("""SELECT
            has_table_privilege(current_user,'workshop_source_correction_audits','SELECT') AS readable,
            has_table_privilege(current_user,'workshop_source_correction_audits','INSERT') AS writable""").fetchone()
        assert permissions == {'readable':True,'writable':False}
        connection.execute("""INSERT INTO workshop_confirmation_audits
            (audit_id,task_id,action,reason,operated_by_id,operated_by_name,operated_by_role,operated_at,scope_machine_no)
            VALUES ('legacy','historical-task','confirm','历史操作记录',%s,'管理员','admin','2026-09-30','Cutter01')""",
            (repository.test_admin_id,))
    restarted = WorkshopRepository(state_database,scope_machine_no='Cutter01')
    assert restarted.list_confirmation_audits()[0]['reason'] == '历史操作记录'


def test_snapshot_sync_appends_new_task_ids_idempotently(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    initial = repository.create_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                             records=[record('A'), record('B')],
                                             user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00')
    assert initial['item_count'] == 2
    first = repository.confirm_tasks(task_ids=['A'], user={'user_id': repository.test_worker_id, 'display_name': '工人', 'role': 'worker'},
                                     report_id=initial['report_id'], now='2026-10-01T02:00:00')
    assert first[0]['task_id'] == 'A'
    synced = repository.populate_empty_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                                    records=[record('A'), record('B'), record('C')],
                                                    user={'user_id': repository.test_admin_id, 'display_name': '补采'}, now='2026-10-02T01:00:00')
    assert synced['item_count'] == 3
    assert synced['confirmed_count'] == 1
    assert [row.task_id for row in repository.get_daily_report_records('2026-09-30')[1]] == ['A', 'B', 'C']
    repeated = repository.populate_empty_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                                      records=[record('A'), record('B'), record('C')],
                                                      user={'user_id': repository.test_admin_id, 'display_name': '再次补采'}, now='2026-10-03T01:00:00')
    assert repeated['item_count'] == 3
    assert len(repository.get_daily_report_records('2026-09-30')[1]) == 3
    assert repository.list_confirmation_audits(task_id='A')[0]['action'] == 'confirm'


def test_snapshot_source_identity_must_match(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    report = repository.create_daily_report(report_date='2026-09-30', source_mode='demo',
                                            records=[record('A')], user={'user_id': repository.test_admin_id, 'display_name': 'u'},
                                            now='2026-10-01T01:00:00')
    with pytest.raises(DailyReportSourceMismatchError):
        repository.populate_empty_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                               records=[record('B')], user={'user_id': repository.test_admin_id, 'display_name': 'u'},
                                               now='2026-10-01T02:00:00')
    assert repository.get_daily_report(report_date='2026-09-30')['item_count'] == 1


def test_snapshot_backfills_only_missing_project_number_and_preserves_confirmation(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    arguments = dict(report_date='2026-09-30', source_mode='sqlserver',
                     user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00')
    report = repository.create_daily_report(**arguments, records=[record('A', project=None)])
    repository.confirm_tasks(task_ids=['A'], report_id=report['report_id'],
                             user={'user_id': repository.test_worker_id, 'display_name': '工人', 'role': 'worker'},
                             now='2026-10-01T02:00:00')
    before_confirmation = repository.get_confirmations(['A'], report_id=report['report_id'])
    before_audits = repository.list_confirmation_audits(task_id='A')
    _, before = repository.get_daily_report_records('2026-09-30')
    updated = repository.populate_empty_daily_report(**arguments, records=[record('A', project='CMYZ0170')])
    _, after = repository.get_daily_report_records('2026-09-30')
    assert updated['report_id'] == report['report_id']
    assert updated['confirmed_count'] == 1
    # A repaired project identifier must not invent a ship name absent from the source.
    assert (after[0].project_code, after[0].ship_no) == ('CMYZ0170', None)
    assert after[0].snapshot_revision == before[0].snapshot_revision
    assert repository.get_confirmations(['A'], report_id=report['report_id']) == before_confirmation
    assert repository.list_confirmation_audits(task_id='A') == before_audits


@pytest.mark.parametrize('changed', ['work_time', 'existing_project'])
def test_snapshot_backfills_project_and_updates_changed_existing_tasks_together(state_database, tmp_path, changed):
    repository = setup(state_database, tmp_path)
    arguments = dict(report_date='2026-09-30', source_mode='sqlserver',
                     user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00')
    repository.create_daily_report(**arguments, records=[record('A', project=None), record('B', project='P')])
    updated = record('B', work=90) if changed == 'work_time' else record('B', project='OTHER')
    repository.populate_empty_daily_report(**arguments, records=[record('A', project='CMYZ0170'), updated])
    _, after = repository.get_daily_report_records('2026-09-30')
    assert [(row.project_code, row.work_time) for row in after] == [
        ('CMYZ0170', Decimal('60')), (updated.project_code, updated.work_time)]
    assert [row.snapshot_revision for row in after] == [1, 2]


def test_empty_snapshot_can_be_populated_and_then_incrementally_synced(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    report = repository.create_daily_report(report_date='2026-09-30', source_mode='sqlserver', records=[],
                                            user={'user_id': repository.test_admin_id, 'display_name': 'u'}, now='2026-10-01T01:00:00')
    populated = repository.populate_empty_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                                       records=[record('A')], user={'user_id': repository.test_admin_id, 'display_name': 'u'},
                                                       now='2026-10-01T02:00:00')
    assert populated['item_count'] == 1
    populated = repository.populate_empty_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                                       records=[record('A'), record('B')], user={'user_id': repository.test_admin_id, 'display_name': 'u'},
                                                       now='2026-10-01T03:00:00')
    assert populated['item_count'] == 2


def test_scope_change_does_not_overwrite_confirmed_snapshot(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    report = repository.create_daily_report(
        report_date='2026-09-30', source_mode='sqlserver', records=[record('A')],
        user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00',
        scope_machine_no='Cutter01',
    )
    repository.confirm_tasks(
        task_ids=['A'],
        user={'user_id': repository.test_worker_id, 'display_name': '工人', 'role': 'worker'},
        report_id=report['report_id'], now='2026-10-01T02:00:00',
    )
    with pytest.raises(DailyReportScopeMismatchError):
        repository.populate_empty_daily_report(
            report_date='2026-09-30', source_mode='sqlserver', records=[record('B')],
            user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-02T01:00:00',
            scope_machine_no='Cutter02',
        )
    assert [row.task_id for row in repository.get_daily_report_records('2026-09-30')[1]] == ['A']
    assert repository.list_confirmation_audits(task_id='A')[0]['action'] == 'confirm'


def test_scope_change_rejects_orphaned_report_confirmation(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    report = repository.create_daily_report(
        report_date='2026-09-30', source_mode='sqlserver', records=[record('A')],
        user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00',
        scope_machine_no='Cutter01',
    )
    repository.confirm_tasks(
        task_ids=['A'],
        user={'user_id': repository.test_worker_id, 'display_name': '工人', 'role': 'worker'},
        report_id=report['report_id'], now='2026-10-01T02:00:00',
    )
    # Simulate a legacy cleanup that removed the item but left its audit/confirmation.
    with repository.connect() as connection:
        connection.execute(
            'DELETE FROM workshop_report_items WHERE report_id = %s',
            (report['report_id'],),
        )

    with pytest.raises(DailyReportScopeMismatchError):
        repository.populate_empty_daily_report(
            report_date='2026-09-30', source_mode='sqlserver', records=[record('B')],
            user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-02T01:00:00',
            scope_machine_no='Cutter02',
        )
    assert repository.get_confirmations(['A'], report_id=report['report_id'])['A']['report_id'] == report['report_id']


def test_changed_source_updates_existing_task_and_appends_new_tasks_idempotently(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    repository.create_daily_report(
        report_date='2026-09-30', source_mode='sqlserver', records=[record('A')],
        user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00',
        scope_machine_no='Cutter01',
    )
    same_value_different_scale = record('A', work='60.0')
    assert repository.populate_empty_daily_report(
        report_date='2026-09-30', source_mode='sqlserver',
        records=[same_value_different_scale],
        user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T02:00:00',
        scope_machine_no='Cutter01',
    )['item_count'] == 1
    corrected = record('A', work=90)
    updated = repository.populate_empty_daily_report(
        report_date='2026-09-30', source_mode='sqlserver',
        records=[corrected, record('B')],
        user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-02T01:00:00',
        scope_machine_no='Cutter01',
    )
    _, records = repository.get_daily_report_records('2026-09-30')
    assert [row.task_id for row in records] == ['A', 'B']
    assert records[0].work_time == Decimal('90') and records[0].snapshot_revision == 2
    assert records[1].snapshot_revision == 1
    assert updated['generated_at'] == '2026-10-02T01:00:00'
    repeated = repository.sync_daily_report(report_date='2026-09-30', source_mode='sqlserver',
        records=[corrected, record('B')], user={'user_id': repository.test_admin_id, 'display_name': '管理员'},
        now='2026-10-03T01:00:00')
    assert repeated == updated
    assert repository.get_daily_report_records('2026-09-30')[1] == records


@pytest.mark.parametrize('confirmed', [False, True])
def test_production_field_updates_preserve_task_identity_and_confirmation_history(state_database, tmp_path, confirmed):
    repository = setup(state_database, tmp_path)
    admin = {'user_id': repository.test_admin_id, 'display_name': '管理员'}
    worker = {'user_id': repository.test_worker_id, 'display_name': '工人', 'role': 'worker'}
    source = replace(record('30632', work=2120), project_code='CMYZ0184', segment_name='406P',
        program_no='S406P130A602.gen', cut_length=Decimal('39.157'), groove_length=Decimal('0'),
        production_time=datetime(2026,11,6,9,10,22), production_date=date(2026,11,6))
    arguments = dict(report_date='2026-11-06', source_mode='sqlserver', user=admin)
    report = repository.sync_daily_report(**arguments, records=[source], now='2026-11-06T10:00:00')
    if confirmed:
        repository.confirm_task(task_id='30632', report_id=report['report_id'], user=worker,
                                snapshot_revision=1, now='2026-11-06T10:01:00')
    confirmation = repository.get_confirmations(['30632'])
    audits = repository.list_confirmation_audits(task_id='30632')
    corrected = replace(source, work_time=Decimal('2263'), project_code='CMYZ0185', segment_name='407P',
        program_no='corrected.gen', cut_length=Decimal('40'), groove_length=Decimal('1'),
        production_time=datetime(2026,11,6,9,11,22), remark='源数据后补')
    repository.sync_daily_report(**arguments, records=[corrected], now='2026-11-06T11:00:00')
    updated_report, rows = repository.get_daily_report_records('2026-11-06')
    assert updated_report['report_id'] == report['report_id'] and updated_report['item_count'] == 1
    assert updated_report['confirmed_count'] == int(confirmed)
    for field in ('task_id','project_code','segment_name','program_no','cut_length','groove_length',
                  'work_time','production_time','production_date','remark'):
        assert getattr(rows[0], field) == getattr(corrected, field)
    assert rows[0].snapshot_revision == 2
    assert repository.get_confirmations(['30632']) == confirmation
    assert repository.list_confirmation_audits(task_id='30632') == audits
    for revision in (None, 1):
        with pytest.raises(ConfirmationConflictError, match='版本已变化'):
            repository.confirm_task(task_id='30632', report_id=report['report_id'], user=worker,
                                    snapshot_revision=revision, now='2026-11-06T11:01:00')
    repository.confirm_task(task_id='30632', report_id=report['report_id'], user=worker,
                            snapshot_revision=2, now='2026-11-06T11:02:00')


@pytest.mark.parametrize('corrected', [False, True])
def test_http_generation_and_task_list_accept_changed_production_data(state_database, tmp_path, monkeypatch, corrected):
    from app.api.auth import require_user
    from app.api.workshop import router
    from tests.line_fixtures import cutting_settings
    from app.models.domain import QueryBundle

    repository = setup(state_database, tmp_path)
    admin = {'user_id': repository.test_admin_id, 'username': 'admin', 'display_name': '管理员', 'role': 'admin', 'active': True}
    cfg = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver', demo_mode=False)
    monkeypatch.setattr('app.api.workshop.settings', cfg)
    repository.sync_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                 records=[record('A')], user=admin, now='2026-10-01T01:00:00')
    repository.confirm_task(task_id='A', user=admin, report_id=repository.get_daily_report('2026-09-30')['report_id'],
                            now='2026-10-01T02:00:00')
    app = FastAPI()
    app.include_router(router)
    app.state.workshop = repository
    app.state.query_service = SimpleNamespace(prepare_workshop_data=lambda _: QueryBundle(
        [], [record('A', work=90 if corrected else 60), record('B')], [],
    ), enrich_ship_names=lambda records: records)
    app.state.lines = as_line_services({'Cutter01': SimpleNamespace(
        settings=cutting_settings(data_source_mode='sqlserver'), workshop=app.state.workshop,
        query_service=getattr(app.state, 'query_service', None))})
    app.dependency_overrides[require_user] = lambda: admin

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'snapshot-sync'
        return await call_next(request)

    with TestClient(app) as client:
        response = client.post('/api/v1/workshop/reports?device_id=Cutter01', json={'report_date': '2026-09-30'})
        assert response.status_code == 200
        assert response.json()['data']['item_count'] == 2
        assert response.json()['data']['confirmed_count'] == 1
        repeated = client.post('/api/v1/workshop/reports?device_id=Cutter01', json={'report_date': '2026-09-30'})
        assert repeated.json()['data']['item_count'] == 2
        tasks = client.get('/api/v1/workshop/tasks?device_id=Cutter01&report_date=2026-09-30')
        assert tasks.status_code == 200
        assert tasks.json()['data']['total'] == 2
        task = next(row for row in tasks.json()['data']['items'] if row['task_id'] == 'A')
        assert task['work_time'] == (90 if corrected else 60)
        assert task['confirmed_by'] == '管理员'
    _, stored = repository.get_daily_report_records('2026-09-30')
    assert [row.task_id for row in stored] == ['A', 'B']
    assert stored[0].work_time == Decimal('90' if corrected else '60')
    assert len(repository.list_confirmation_audits(task_id='A')) == 1


def test_automation_preparation_syncs_existing_snapshot(state_database, tmp_path):
    from tests.line_fixtures import cutting_settings
    from app.database.automation import AutomationRepository
    from app.database.metadata import MetadataRepository
    from app.models.domain import QueryBundle
    from app.services.automation_service import AutomationService

    workshop = setup(state_database, tmp_path)
    cfg = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver', demo_mode=False)
    metadata = MetadataRepository(workshop.database, device_id='Cutter01')
    automation = AutomationRepository(workshop.database)
    user = {'user_id': workshop.test_admin_id, 'display_name': '管理员'}
    workshop.sync_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                              records=[record('A')], user=user, now='2026-10-01T01:00:00')
    service = AutomationService(cfg, automation, metadata, object())
    service.line_services = as_line_services({'Cutter01': SimpleNamespace(settings=cfg, workshop=workshop, query_service=SimpleNamespace(fetch_workshop_data=lambda _: QueryBundle(
        [], [record('A', work=90), record('B')], [],
    )))})
    run = {'device_id': 'Cutter01', 'report_date': '2026-09-30', 'created_by_id': user['user_id'], 'created_by_name': user['display_name']}
    service._ensure_workshop_report(run, datetime(2026, 10, 1, 2), service.line_services["Cutter01"].cutting())
    service._ensure_workshop_report(run, datetime(2026, 10, 1, 3), service.line_services["Cutter01"].cutting())
    assert [row.task_id for row in workshop.get_daily_report_records('2026-09-30')[1]] == ['A', 'B']
    assert workshop.get_daily_report_records('2026-09-30')[1][0].work_time == Decimal('90')


def test_concurrent_first_sync_preserves_union_of_tasks(state_database, tmp_path):
    repository = setup(state_database, tmp_path)
    barrier = Barrier(2)

    def sync(task_id):
        barrier.wait(timeout=5)
        return repository.sync_daily_report(
            report_date='2026-09-30', source_mode='sqlserver', records=[record(task_id)],
            user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-01T01:00:00',
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(sync, ['A', 'B']))
    assert reports[0]['report_id'] == reports[1]['report_id']
    state, rows = repository.get_daily_report_records('2026-09-30')
    assert state['item_count'] == 2
    assert {row.task_id for row in rows} == {'A', 'B'}
