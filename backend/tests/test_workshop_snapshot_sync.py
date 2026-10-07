from datetime import date, datetime, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from app.database.workshop import (
    DailyReportRecordMismatchError,
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


def setup(tmp_path):
    repository = WorkshopRepository(tmp_path / 'meta.db')
    repository.initialize()
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


def test_initialization_does_not_create_source_correction_records_or_delete_legacy_data(tmp_path):
    repository = setup(tmp_path)
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'workshop_source_correction_audits'"
        ).fetchone() is None
        connection.execute('CREATE TABLE workshop_source_correction_audits (audit_id TEXT PRIMARY KEY, payload TEXT)')
        connection.execute("INSERT INTO workshop_source_correction_audits VALUES ('legacy', '历史操作记录')")
    repository.initialize()
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT payload FROM workshop_source_correction_audits WHERE audit_id = 'legacy'"
        ).fetchone()[0] == '历史操作记录'


def test_snapshot_sync_appends_new_task_ids_idempotently(tmp_path):
    repository = setup(tmp_path)
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


def test_snapshot_source_identity_must_match(tmp_path):
    repository = setup(tmp_path)
    report = repository.create_daily_report(report_date='2026-09-30', source_mode='demo',
                                            records=[record('A')], user={'user_id': repository.test_admin_id, 'display_name': 'u'},
                                            now='2026-10-01T01:00:00')
    with pytest.raises(DailyReportSourceMismatchError):
        repository.populate_empty_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                                               records=[record('B')], user={'user_id': repository.test_admin_id, 'display_name': 'u'},
                                               now='2026-10-01T02:00:00')
    assert repository.get_daily_report(report_date='2026-09-30')['item_count'] == 1


def test_empty_snapshot_can_be_populated_and_then_incrementally_synced(tmp_path):
    repository = setup(tmp_path)
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


def test_scope_change_does_not_overwrite_confirmed_snapshot(tmp_path):
    repository = setup(tmp_path)
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


def test_scope_change_rejects_orphaned_report_confirmation(tmp_path):
    repository = setup(tmp_path)
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
            'DELETE FROM workshop_report_items WHERE report_id = ?',
            (report['report_id'],),
        )

    with pytest.raises(DailyReportScopeMismatchError):
        repository.populate_empty_daily_report(
            report_date='2026-09-30', source_mode='sqlserver', records=[record('B')],
            user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-02T01:00:00',
            scope_machine_no='Cutter02',
        )
    assert repository.get_confirmations(['A'], report_id=report['report_id'])['A']['report_id'] == report['report_id']


def test_unexpected_source_mismatch_is_reported_without_silent_overwrite(tmp_path):
    repository = setup(tmp_path)
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
    with pytest.raises(DailyReportRecordMismatchError, match='日报任务与生产源数据不一致'):
        repository.populate_empty_daily_report(
            report_date='2026-09-30', source_mode='sqlserver',
            records=[corrected, record('B')],
            user={'user_id': repository.test_admin_id, 'display_name': '管理员'}, now='2026-10-02T01:00:00',
            scope_machine_no='Cutter01',
        )
    _, records = repository.get_daily_report_records('2026-09-30')
    assert [row.task_id for row in records] == ['A']
    assert records[0].work_time == Decimal('60')


@pytest.mark.parametrize('corrected', [False, True])
def test_http_generation_syncs_nonempty_snapshot_or_reports_source_conflict(tmp_path, monkeypatch, corrected):
    from app.api.auth import require_user
    from app.api.workshop import router
    from app.config import Settings
    from app.models.domain import QueryBundle

    repository = setup(tmp_path)
    admin = {'user_id': repository.test_admin_id, 'display_name': '管理员', 'role': 'admin'}
    cfg = Settings(project_root=tmp_path, data_source_mode='sqlserver', demo_mode=False)
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
    ))
    app.dependency_overrides[require_user] = lambda: admin

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'snapshot-sync'
        return await call_next(request)

    with TestClient(app) as client:
        response = client.post('/api/v1/workshop/reports', json={'report_date': '2026-09-30'})
        assert response.status_code == (409 if corrected else 200)
        if not corrected:
            assert response.json()['data']['item_count'] == 2
            assert response.json()['data']['confirmed_count'] == 1
            repeated = client.post('/api/v1/workshop/reports', json={'report_date': '2026-09-30'})
            assert repeated.json()['data']['item_count'] == 2
    _, stored = repository.get_daily_report_records('2026-09-30')
    assert [row.task_id for row in stored] == (['A'] if corrected else ['A', 'B'])
    assert stored[0].work_time == Decimal('60')
    assert len(repository.list_confirmation_audits(task_id='A')) == 1


def test_automation_preparation_syncs_existing_snapshot(tmp_path):
    from app.config import Settings
    from app.database.automation import AutomationRepository
    from app.database.metadata import MetadataRepository
    from app.models.domain import QueryBundle
    from app.services.automation_service import AutomationService

    workshop = setup(tmp_path)
    cfg = Settings(project_root=tmp_path, data_source_mode='sqlserver', demo_mode=False)
    metadata = MetadataRepository(workshop.database)
    metadata.initialize()
    automation = AutomationRepository(workshop.database)
    automation.initialize()
    user = {'user_id': workshop.test_admin_id, 'display_name': '管理员'}
    workshop.sync_daily_report(report_date='2026-09-30', source_mode='sqlserver',
                              records=[record('A')], user=user, now='2026-10-01T01:00:00')
    service = AutomationService(cfg, automation, metadata, object())
    service.query_service = SimpleNamespace(fetch_workshop_data=lambda _: QueryBundle(
        [], [record('A'), record('B')], [],
    ))
    run = {'report_date': '2026-09-30', 'created_by_id': user['user_id'], 'created_by_name': user['display_name']}
    service._ensure_workshop_report(run, datetime(2026, 10, 1, 2))
    service._ensure_workshop_report(run, datetime(2026, 10, 1, 3))
    assert [row.task_id for row in workshop.get_daily_report_records('2026-09-30')[1]] == ['A', 'B']


def test_concurrent_first_sync_preserves_union_of_tasks(tmp_path):
    repository = setup(tmp_path)
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
