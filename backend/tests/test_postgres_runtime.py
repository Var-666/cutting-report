"""P5 state/transaction/concurrency checks against an isolated PostgreSQL 18 server.

Opt in with P5_TEST_POSTGRES=1; the configured admin must be a disposable test
server. Each test owns a fresh database, never an application's live database.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timezone, timedelta
import json
import multiprocessing as mp
import os
from pathlib import Path
from threading import Barrier
from time import sleep
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from app.database.postgres import PostgresDatabase, ExecutionLeaseLost, execution_scope
from app.database.postgres_migrations import apply_migrations
from app.database.coordination import CoordinationRepository
from app.database.unit_of_work import UnitOfWork
from app.database.users import UserRepository
from app.database.sessions import SessionRepository
from app.database.metadata import MetadataRepository
from app.database.downloads import DownloadRepository
from app.database.energy import EnergyReportRepository
from app.database.report_snapshots import ReportSnapshotRepository, ReportKey, ReportSnapshot, fingerprint
from app.database.llm_usage import LlmUsageRepository
from app.models.conditions import ConditionGroup, ConditionRule
from tests.line_fixtures import configuration

pytestmark = pytest.mark.skipif(os.environ.get('P5_TEST_POSTGRES') != '1', reason='requires isolated real PostgreSQL')
from tests.postgres_fixtures import postgres_state as state, PASSWORD, PORT

def new_user(repository, name='operator'):
    return repository.create_user(username=name, display_name='测试工人', password='p5-test-password',
                                  role='worker', now='2026-11-04T08:00:00+08:00')


def test_uow_commit_rollback_and_shared_pool(state):
    database, cfg, _ = state
    now = datetime.now(timezone.utc)
    with UnitOfWork(database) as unit:
        user = new_user(unit.users)
        token, _ = unit.sessions.create_session(user['user_id'], now=now, hours=1)
        assert unit.users._connection is unit.sessions._connection is unit.connection
        # A bound repository must not commit the outer transaction.
        with PostgresDatabase(cfg) as other:
            assert SessionRepository(other).user_for_session(token, now=now.isoformat()) is None
    assert SessionRepository(database).user_for_session(token, now=now.isoformat())['user_id'] == user['user_id']
    assert UserRepository(database).authenticate('OPERATOR', 'p5-test-password')['user_id'] == user['user_id']
    with pytest.raises(RuntimeError, match='rollback'):
        with UnitOfWork(database) as unit:
            failed = new_user(unit.users, 'rollback-user')
            unit.sessions.create_session(failed['user_id'], now=now, hours=1)
            raise RuntimeError('rollback')
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM users WHERE username='rollback-user'").fetchone() is None
    assert database.healthy()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with database.connect() as connection:
            connection.execute('CREATE TABLE forbidden(id bigint)')
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with database.connect() as connection:
            connection.execute('DELETE FROM _postgres_schema_migrations')


def _acquire_process(cfg, start, results):
    with PostgresDatabase(cfg) as database:
        start.wait(10)
        results.put(CoordinationRepository(database).acquire('worker:test-consumer', ttl=10))


def test_lease_is_exclusive_between_processes(state):
    _, cfg, _ = state
    context = mp.get_context('spawn')
    start, results = context.Event(), context.Queue()
    processes = [context.Process(target=_acquire_process, args=(cfg, start, results)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        start.set()
        owners = [results.get(timeout=20) for _ in processes]
        assert sum(owner is not None for owner in owners) == 1
    finally:
        for process in processes:
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
        results.close()


def test_concurrent_claim_dedup_and_one_operation_per_line(state):
    database, _, _ = state
    queue = CoordinationRepository(database)
    owner = queue.acquire('worker:test-consumer', ttl=10)
    assert queue.enqueue('refresh', 'Cutter01', {}, key='same')
    assert not queue.enqueue('refresh', 'Cutter01', {}, key='same')
    assert queue.enqueue('refresh', 'Cutter01', {'page': 2}, key='other')
    barrier = Barrier(2)
    def claim():
        barrier.wait(5)
        return queue.claim_task(owner, analysis=False)
    with ThreadPoolExecutor(2) as executor:
        claimed = list(executor.map(lambda _: claim(), range(2)))
    tasks = [task for task in claimed if task]
    assert len(tasks) == 1
    assert queue.task_counts() == {'running': 1, 'pending': 1}
    assert queue.finish_task(tasks[0], owner)
    assert queue.claim_task(owner, analysis=False)['task_id'] != tasks[0]['task_id']


def test_expired_owner_cannot_commit_finish_or_recover(state):
    database, _, _ = state
    queue = CoordinationRepository(database)
    first = queue.acquire('worker:test-consumer', ttl=0.2)
    queue.enqueue('refresh', 'Cutter01', {}, key='takeover')
    task = queue.claim_task(first, analysis=False)
    with pytest.raises(ExecutionLeaseLost):
        with execution_scope(database, 'worker:test-consumer', first), database.connect() as connection:
            connection.execute("INSERT INTO llm_waiters VALUES ('must-roll-back', 1)")
            sleep(0.25)
    second = queue.acquire('worker:test-consumer', ttl=10)
    assert second and second != first
    assert not queue.renew('worker:test-consumer', first, ttl=10)
    with pytest.raises(ExecutionLeaseLost):
        queue.finish_task(task, first)
    with pytest.raises(ExecutionLeaseLost):
        queue.recover_tasks(first)
    queue.recover_tasks(second)
    queue.admit_deferred()
    taken = queue.claim_task(second, analysis=False)
    assert taken['task_id'] == task['task_id']
    queue.finish_task(taken, second)
    with database.connect() as connection:
        assert connection.execute("SELECT 1 FROM llm_waiters WHERE waiter_id='must-roll-back'").fetchone() is None
        assert connection.execute('SELECT status FROM background_tasks WHERE task_id=%s', (task['task_id'],)).fetchone()['status'] == 'completed'


def test_download_conditions_snapshots_energy_and_llm(state):
    database, cfg, _ = state
    now = datetime.now(timezone.utc)
    downloads = DownloadRepository(database)
    item = dict(download_id='download-1', document_id='doc-1', status='downloaded',
        created_at=now.isoformat(), updated_at=now.isoformat(), filename='A_100%.TXT', size=100,
        project_id='project', catalog_id='catalog', version_id='v1', relative_path='a.txt')
    downloads.save(item)
    filters = ConditionGroup(rules=[ConditionRule(field='filename', operator='contains', value='a_100%')])
    assert downloads.list(1, 20, filters)[0] == [item]
    assert downloads.find_latest('doc-1', 'v1', 'a.txt') == item
    assert downloads.list_cleanup_candidates((now+timedelta(days=1)).isoformat()) == [item]
    report = {'report_date':'2026-11-04', 'updated_at':now.isoformat(), 'total':12.3}
    energy = EnergyReportRepository(database)
    assert energy.save_day('meters', report, previous=None)
    assert not energy.save_day('meters', report | {'total':15}, previous=None)
    assert energy.get_day('meters','2026-11-04') == report
    energy.enqueue_months('meters', ['2026-11'], now.isoformat())
    assert energy.claim_history_month('meters', now.isoformat())['report_month'] == '2026-11'
    snapshots = ReportSnapshotRepository(database)
    key = ReportKey('welding','page:daily','2026-11-01','2026-11-04',{},'source')
    payload = {'summary':{'count':4},'history':['历史']}
    snapshots.save(ReportSnapshot(key,payload,fingerprint(payload),now,now,now+timedelta(minutes=1)))
    assert snapshots.get(key).payload == payload
    assert snapshots.get(replace(key,source_fingerprint='other')) is None
    usage = LlmUsageRepository(database)
    usage.initialize()
    usage.record_call(12, occurred_at=now, scene='welding', input_tokens=4, output_tokens=8)
    assert usage.summary() == {'call_count':1,'metered_count':1,'total_tokens':12}
    assert usage.report(now.date(),now.date())['summary']['total_tokens'] == 12
    from app.services.model_coordination import ModelSlot
    slot = ModelSlot(cfg, database, background=False, promoted=False, budget=1)
    slot.register()
    with database.connect() as connection:
        assert connection.execute('SELECT waiter_id FROM llm_waiters').fetchone()['waiter_id'] == slot.waiter
    slot.close()
    with database.connect() as connection:
        assert connection.execute('SELECT 1 FROM llm_waiters').fetchone() is None


def test_workshop_confirmation_serializes_and_audits(state):
    from decimal import Decimal
    from app.database.workshop import WorkshopRepository, ConfirmationConflictError
    from app.models.domain import WorkshopRecord
    database, _, _ = state
    repo = WorkshopRepository(database, scope_machine_no='Cutter01')
    now = '2026-11-04T08:00:00+08:00'
    users = [new_user(repo, 'worker-'+str(index)) for index in range(2)]
    record = WorkshopRecord(task_id='task-1', project_code='P1', ship_no='船名', segment_name='301C',
        program_no='1.gen', cut_length=Decimal('5.1'), groove_length=Decimal('0'), work_time=Decimal('90'))
    report = repo.create_daily_report(report_date='2026-11-04',source_mode='demo',records=[record],user=users[0],now=now)
    assert repo.get_daily_report('2026-11-04')['report_id'] == report['report_id']
    barrier = Barrier(2)
    def confirm(user):
        barrier.wait(5)
        try:
            return repo.confirm_tasks(task_ids=['task-1'],user=user,now=now,report_id=report['report_id'])
        except ConfirmationConflictError:
            return None
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(confirm, users))
    assert sum(result is not None for result in results) == 1
    with database.connect() as connection:
        winner = connection.execute('SELECT worker_id FROM workshop_confirmations').fetchone()['worker_id']
        assert connection.execute('SELECT COUNT(*) AS n FROM workshop_confirmation_audits').fetchone()['n'] == 1
    selected = next(user for user in users if user['user_id']==winner)
    repo.revoke_confirmation(task_id='task-1',report_id=report['report_id'],user=selected,reason='误认',now=now)
    assert repo.get_daily_report('2026-11-04')['confirmed_count'] == 0
    assert repo.get_daily_report_records('2026-11-04')[1][0].ship_no == '船名'


def test_automation_email_alarm_dispatch_and_retention(state):
    from app.database.automation import AutomationRepository
    from app.database.alarms import AlarmRepository
    from app.database.definitions import DefinitionRepository
    from app.database.dispatch import DispatchRepository
    from app.database.retention import RetentionRepository
    database, _, _ = state
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec='seconds')
    user = new_user(UserRepository(database))
    defs = DefinitionRepository(database)
    defs.initialize()
    assert len(defs.list_definitions()) == 3
    auto = AutomationRepository(database)
    schedule = auto.create_schedule(dict(name='日报',report_type='workshop',frequency='daily',run_time='08:00',
        report_period='previous_day',recipients=['test@example.test'],subject_template='日报',chart_type='bar',
        attach_report=True,max_attempts=3,enabled=True,next_run_at=stamp,device_id='Cutter01'),user=user,now=stamp)
    run = auto.reserve_run(schedule, scheduled_for=stamp, report_date='2026-11-04',trigger_type='scheduled',now=stamp,
                           next_run_at=(now+timedelta(days=1)).isoformat())
    assert run
    assert auto.reserve_run(schedule,scheduled_for=stamp,report_date='2026-11-04',trigger_type='scheduled',now=stamp,next_run_at=stamp) is None
    metadata = MetadataRepository(database,device_id='Cutter01')
    metadata.create_job(report_id='report-1',report_date='2026-11-04',project_code=None,ship_no=None,request_id='request-1',created_at=stamp)
    metadata.save_report(report_id='report-1',filename='1.html',relative_path='1.html',file_size=10,sha256='abc',task_count=1,generated_at=stamp,system_version='test')
    auto.attach_report(run['run_id'],'report-1',now=stamp)
    delivery = auto.enqueue_delivery('report-1',subject='日报',now=stamp)
    assert delivery
    queue = CoordinationRepository(database)
    owner = queue.acquire('worker:test-consumer',ttl=10)
    with execution_scope(database,'worker:test-consumer',owner):
        claimed = auto.claim_delivery(stamp)
        assert claimed['delivery_id'] == delivery['delivery_id']
        assert auto.claim_delivery(stamp) is None
        auto.mark_delivery_sent(claimed,message_id='test-message',now=stamp)
    assert auto.delivery_state(delivery['delivery_id']) == 'sent'
    alarms = AlarmRepository(database,machine_no='Cutter01',source_name='test-source')
    alarms.initialize()
    assert alarms.try_acquire_lease('alarm-owner',now)
    alarms.ingest([dict(alarm_info_id=1,machine_no='Cutter01',alarm_type='PLC',alarm_code='A1',alarm_desc='测试',
        start_time=stamp,remove_time=None)],now=now,cursor=1,debounce_minutes=10,lease_owner='alarm-owner')
    incidents = alarms.list_incidents("open")
    assert len(incidents) == 1
    report_id = alarms.reserve_report(proposed_report_id='alarm-1',emission_key='emission-1',machine_no='Cutter01',
        incident_ids=[incidents[0]['incident_id']],now=stamp,lease_owner='alarm-owner',lease_now=now)
    assert alarms.get_report(report_id)['status'] == 'generating'
    assert alarms.page_reports(monitor_start_at=None,page=1,page_size=20)['total'] == 1
    dispatches = DispatchRepository(database)
    item = dict(document_id='doc',project_id='project',document_no='test.TXT',relative_path='test.TXT',
        machine_ip='127.0.0.1',quantity=1,status='ready',source_mode='mock')
    first, created = dispatches.create_unless_active(item,user=user,now=stamp)
    assert created
    same, created = dispatches.create_unless_active(item,user=user,now=stamp)
    assert not created and same['dispatch_id'] == first['dispatch_id']
    assert dispatches.transition(first['dispatch_id'],expected=['ready'],status='sent',now=stamp)['status'] == 'sent'
    assert dispatches.list(page=1,page_size=20,sent_start=(now-timedelta(days=1)).isoformat(),sent_end=(now+timedelta(days=1)).isoformat())[1] == 1
    assert dispatches.daily_sent_counts(sent_start=(now-timedelta(days=1)).isoformat(),sent_end=(now+timedelta(days=1)).isoformat(),timezone='Asia/Shanghai',machine_ip=None)
    retention = RetentionRepository(database)
    assert retention.cleanup_expired_records(report_cutoff='2000-01-01',analysis_cutoff='2000-01-01',alarm_cutoff='2000-01-01',
        dispatch_cutoff='2000-01-01',audit_cutoff='2000-01-01',queued_at=stamp,batch_size=100)['reports'] == 0


def _run_worker_process(cfg, ready, stop, results):
    import sqlite3
    import sys
    def forbidden(*args, **kwargs):
        raise AssertionError('formal Worker must never open SQLite')
    sqlite3.connect = forbidden
    from app.worker import WorkerProcess
    worker = WorkerProcess(cfg)
    try:
        worker.start()
        assert 'app.database.sqlite_history' not in sys.modules
        ready.set()
        while not stop.wait(0.1):
            worker.tick()
        results.put('ok')
    except BaseException as exc:
        results.put(type(exc).__name__ + ': ' + str(exc))
    finally:
        worker.stop()


def test_web_and_worker_process_share_state_without_sqlite(state, monkeypatch):
    import shutil
    import sqlite3
    from fastapi.testclient import TestClient
    import app.main as web
    import app.api.auth as auth
    import app.api.reports as reports
    database, cfg, _ = state
    (cfg.project_root/'vendor').mkdir()
    shutil.copyfile('vendor/echarts.min.js', cfg.echarts_vendor_path)
    monkeypatch.setattr(web, 'settings', cfg)
    monkeypatch.setattr(auth, 'settings', cfg)
    monkeypatch.setattr(reports, 'settings', cfg)
    def forbidden(*args, **kwargs):
        raise AssertionError('formal Web must never open SQLite')
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    context = mp.get_context('spawn')
    ready, stop, results = context.Event(), context.Event(), context.Queue()
    worker = context.Process(target=_run_worker_process, args=(cfg, ready, stop, results))
    try:
        with TestClient(web.app) as client:
            worker.start()
            assert ready.wait(15)
            response = client.post('/api/v1/auth/bootstrap', json={'username':'admin','password':'p5-admin-password','display_name':'管理员'})
            assert response.status_code == 200, response.text
            assert client.get('/api/v1/auth/status').json()['data']['authenticated']
            accepted = client.post('/api/v1/reports?device_id=Cutter01', json={'report_type':'workshop','report_date':'2026-11-03'})
            assert accepted.status_code == 202, accepted.text
            report_id = accepted.json()['data']['report_id']
            repository = MetadataRepository(database, device_id='Cutter01')
            for _ in range(100):
                job = repository.get_job(report_id)
                if job['status'] in ('completed', 'failed'):
                    break
                sleep(0.1)
            assert job['status'] == 'completed', job
            response = client.get(f'/api/v1/reports/{report_id}/status?device_id=Cutter01')
            assert response.status_code == 200 and response.json()['data']['status'] == 'completed'
            with database.connect() as connection:
                assert connection.execute('SELECT relative_path FROM reports WHERE report_id=%s',(report_id,)).fetchone()
            for _ in range(80):
                health = client.get('/api/v1/health')
                if health.status_code == 200 and health.json()['data']['workers'].get('consumer_running_count') == 1:
                    break
                sleep(0.1)
            assert health.status_code == 200 and health.json()['data']['workers']['consumer_running_count'] == 1
            assert not health.json()['data']['background_ready'], 'this fixture starts only the Consumer role'
            assert not list(cfg.data_dir.rglob('*.db')) and not list(cfg.data_dir.rglob('*.sqlite*'))
    finally:
        stop.set()
        if worker.pid:
            worker.join(12)
            if worker.is_alive():
                worker.kill()
                worker.join(5)
    assert worker.exitcode == 0
    assert results.get(timeout=3) == 'ok'
    results.close()


def test_database_unavailable_is_explicit_and_recovery_keeps_state(state, monkeypatch):
    from fastapi.testclient import TestClient
    from psycopg_pool import PoolTimeout
    import app.main as web
    from app.worker import WorkerProcess
    database, cfg, _ = state
    monkeypatch.setattr(web, 'settings', cfg)
    user = new_user(UserRepository(database))
    worker = WorkerProcess(cfg)
    admin_options = dict(host='127.0.0.1',port=PORT,user='postgres',password=PASSWORD,dbname='p5_test')
    worker.start()
    try:
        with TestClient(web.app) as client, psycopg.connect(**admin_options,autocommit=True) as admin:
            admin.execute(sql.SQL('ALTER DATABASE {} CONNECTION LIMIT 0').format(sql.Identifier(cfg.postgres_database)))
            admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",(cfg.postgres_database,))
            try:
                web.app.state.runtime.worker_status_reader._next_read = 0
                response = client.get('/api/v1/health')
                assert response.status_code == 503
                assert response.json()['data']['status'] == 'degraded'
                worker._next_heartbeat = 0
                with pytest.raises((PoolTimeout, psycopg.OperationalError)):
                    worker.tick()
                assert not list(cfg.data_dir.rglob('*.db'))
            finally:
                admin.execute(sql.SQL('ALTER DATABASE {} CONNECTION LIMIT -1').format(sql.Identifier(cfg.postgres_database)))
            for _ in range(20):
                try:
                    assert database.healthy()
                    break
                except (PoolTimeout, psycopg.OperationalError):
                    sleep(0.1)
            assert UserRepository(database).authenticate(user['username'],'p5-test-password')['user_id'] == user['user_id']
            worker._heartbeat()
            assert CoordinationRepository(database).worker_status('worker:test-consumer')['owner'] == worker.owner
            with database.connect() as connection:
                assert connection.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'] == 1
            web.app.state.runtime.worker_status_reader._next_read = 0
            assert client.get('/api/v1/health').status_code == 200
    finally:
        worker.stop()


def test_expired_snapshot_cleanup_does_not_wait_on_another_line_publication(state):
    database, _, _ = state
    now = datetime.now(timezone.utc)
    repository = ReportSnapshotRepository(database)
    key = ReportKey('other-line','page:daily','2026-01-01','2026-01-01',{},'source')
    old = ReportSnapshot(key,{'count':1},fingerprint({'count':1}),now,now,now-timedelta(days=31))
    # Save an old timestamp directly because normal save immediately prunes it.
    with database.connect() as connection:
        connection.execute('''INSERT INTO report_snapshots
            (line_id,report_type,period_start,period_end,query_fingerprint,source_fingerprint,
             snapshot_schema_version,data_version,data_as_of,refreshed_at,expires_at,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (*key.values,old.data_version,now.isoformat(),now.isoformat(),old.expires_at.isoformat(),json.dumps(old.payload)))
    with ThreadPoolExecutor(1) as executor:
        with database.connect() as connection:
            connection.execute("SELECT * FROM report_snapshots WHERE line_id='other-line' FOR UPDATE")
            live_key = replace(key,line_id='live-line')
            saved = executor.submit(repository.save,ReportSnapshot(live_key,old.payload,old.data_version,now,now,now+timedelta(minutes=1)))
            saved.result(timeout=2)
            assert repository.get(live_key).payload == old.payload
    repository.save(ReportSnapshot(live_key,old.payload,old.data_version,now,now,now+timedelta(minutes=1)))
    assert repository.get(key) is None


def test_alarm_renewal_thread_inherits_worker_execution_fence(state, monkeypatch):
    from threading import Event
    from types import SimpleNamespace
    from app.database.alarms import AlarmRepository
    from app.services.alarm_monitor_service import AlarmMonitorService
    database, _, _ = state
    alarms = AlarmRepository(database,machine_no='Cutter01',source_name='renewal-fence')
    alarms.initialize()
    owner = CoordinationRepository(database).acquire('worker:test-consumer',ttl=0.3)
    service = AlarmMonitorService(SimpleNamespace(),alarms,object(),object(),object())
    monkeypatch.setattr(service,'LEASE_RENEW_SECONDS',0.02)
    observed = Event()
    def external_work(now, alarm_owner, lease_now, lost):
        assert lost.wait(2), 'alarm heartbeat must notice an expired global Worker lease'
        observed.set()
        return {}
    monkeypatch.setattr(service,'_run_owned',external_work)
    def run():
        with execution_scope(database,'worker:test-consumer',owner):
            service.run_once(datetime.now(timezone.utc))
    with ThreadPoolExecutor(1) as executor:
        result = executor.submit(run)
        with pytest.raises(ExecutionLeaseLost):
            result.result(timeout=3)
    assert observed.is_set()
