"""P7 ownership and recovery contracts against real, isolated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from time import monotonic, sleep

import pytest

from app.database.coordination import CoordinationRepository
from app.database.energy import EnergyReportRepository
from app.database.metadata import MetadataRepository
from app.database.postgres import ExecutionLeaseLost, execution_scope
from app.database.postgres_migrations import MigrationError, prepare_consumers
from app.services.worker_status import WorkerStatusReader, background_ready, public_status
from app.worker import WorkerProcess


def consumers(database, count=3):
    queue = CoordinationRepository(database)
    return [(f'worker:test-{i}', queue.acquire(f'worker:test-{i}', ttl=60)) for i in range(count)]


def scope(database, consumer):
    return execution_scope(database, *consumer)


def expire(database, consumer):
    with database.connect() as connection:
        connection.execute('UPDATE process_leases SET expires_at=0 WHERE name=%s', (consumer[0],))


def create_report(metadata, report_id, *, report_type='workshop'):
    metadata.create_job(report_id=report_id, report_date='2026-11-04', project_code=None, ship_no=None,
                        request_id=report_id, created_at='2026-11-04T10:00:00+08:00', device_id='Cutter01', report_type=report_type)


def test_real_consumers_start_with_unique_leases_and_same_instance_is_exclusive(postgres_state):
    database, cfg, _ = postgres_state
    processes = [WorkerProcess(replace(cfg, consumer_instance_id=f'consumer-{i}', worker_poll_seconds=.05)) for i in range(3)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            deadline = monotonic() + 2
            while not process.report_worker.status()['queue'].get('available'):
                assert monotonic() < deadline
                sleep(.01)
            process._heartbeat()
        assert len({process.owner for process in processes}) == 3
        assert all(process.repository.worker_status(process.lease_name)['report_alive'] for process in processes)
        duplicate = WorkerProcess(replace(cfg, consumer_instance_id='consumer-1'))
        with pytest.raises(RuntimeError, match='实例租约'):
            duplicate.start()
        status = WorkerStatusReader(database).status()
        assert status['consumer_running_count'] == 3
        assert len(public_status(status)['consumer_instances']) == 3
        assert 'configuration' not in repr(public_status(status))
        # Scaling down closes just this instance; healthy peers remain available.
        processes[2].stop()
        assert all(process.repository.worker_status(process.lease_name)['state'] == 'running' for process in processes[:2])
        assert WorkerStatusReader(database).status()['consumer_running_count'] == 2
    finally:
        for process in processes:
            process.stop()


def test_atomic_claim_and_same_line_conflicts_do_not_serialize_other_lines(postgres_state):
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    first, second, third = consumers(database)
    queue.enqueue('current_refresh', 'Cutter01', {'source_fingerprint': 'source'}, key='one')
    barrier = Barrier(2)
    def claim(consumer):
        barrier.wait()
        return queue.claim_task(consumer[1], analysis=False)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, [first, second]))
    assert sum(task is not None for task in results) == 1
    queue.enqueue('current_refresh', 'Cutter01', {'source_fingerprint': 'source'}, key='conflict')
    queue.enqueue('current_refresh', 'Cutter02', {'source_fingerprint': 'other'}, key='other')
    task = queue.claim_task(third[1], analysis=False)
    assert task['device_id'] == 'Cutter02'
    assert queue.claim_task(second[1], analysis=False) is None


def test_recovery_only_moves_expired_tasks_and_old_owner_cannot_finish(postgres_state):
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    first, second, third = consumers(database)
    for line in ('Cutter01', 'Cutter02'):
        queue.enqueue('current_refresh', line, {'source_fingerprint': line}, key=line)
    old = queue.claim_task(first[1], analysis=False)
    live = queue.claim_task(second[1], analysis=False)
    queue.recover_tasks(third[1])
    assert queue.task_counts() == {'running': 2}
    expire(database, first)
    queue.recover_tasks(third[1])
    assert queue.task_counts() == {'deferred': 1, 'running': 1}
    queue.admit_deferred()
    taken = queue.claim_task(third[1], analysis=False)
    assert taken['task_id'] == old['task_id'] and taken['attempts'] == old['attempts'] + 1
    with pytest.raises(ExecutionLeaseLost):
        queue.finish_task(old, first[1])
    assert queue.finish_task(taken, second[1]) is None
    assert queue.finish_task(live, second[1]) is not None
    assert queue.finish_task(taken, third[1]) is not None


def test_report_recovery_and_writes_follow_current_owner(postgres_state):
    database, _, _ = postgres_state
    metadata = MetadataRepository(database)
    first, second, third = consumers(database)
    for report_id in ('report-a', 'report-b'):
        create_report(metadata, report_id)
    with scope(database, first):
        old = metadata.claim_next_job('start')
    with scope(database, second):
        live = metadata.claim_next_job('start')
    with scope(database, third):
        assert metadata.recover_interrupted_jobs() == 0
        with pytest.raises(ExecutionLeaseLost):
            metadata.update_status(old['report_id'], 'failed', 'foreign write')
    expire(database, first)
    with scope(database, third):
        assert metadata.recover_interrupted_jobs() == 1
        taken = metadata.claim_next_job('restart')
        assert taken['report_id'] == old['report_id']
        metadata.update_status(taken['report_id'], 'rendering', 'rendering')
        metadata.save_report(report_id=taken['report_id'], filename='report.html', relative_path='new-owner.html',
            file_size=1, sha256='test', task_count=1, generated_at='now', system_version='test')
        metadata.update_status(taken['report_id'], 'completed', 'completed')
        assert metadata.recover_interrupted_jobs() == 0
    with scope(database, first), pytest.raises(ExecutionLeaseLost):
        metadata.update_status(taken['report_id'], 'completed', 'old owner')
    assert metadata.get_job(live['report_id'])['execution_owner'] == second[1]
    assert metadata.get_job(live['report_id'])['status'] == 'querying'
    assert metadata.get_job(taken['report_id'])['relative_path'] == 'new-owner.html'


def test_energy_claims_recover_exactly_one_owner_and_keep_month_identity(postgres_state):
    database, _, _ = postgres_state
    repository = EnergyReportRepository(database)
    first, second, third = consumers(database)
    repository.enqueue_months('source', ['2026-09-01', '2026-10-01'], 'now')
    assert repository.enqueue_months('source', ['2026-09-01', '2026-10-01'], 'now') == 0
    with scope(database, first):
        old = repository.claim_history_month('source', 'now')
    with scope(database, second):
        live = repository.claim_history_month('source', 'now')
    with scope(database, third):
        repository.recover_history_queue('source', 'now')
        assert repository.claim_history_month('source', 'now') is None
    expire(database, first)
    with scope(database, third):
        repository.recover_history_queue('source', 'now')
        taken = repository.claim_history_month('source', 'now')
        assert taken['report_month'] == old['report_month']
        assert taken['claim_token'] != old['claim_token']
        assert not repository.reschedule_history_month(old, 'later', 'now')
        assert repository.reschedule_history_month(taken, 'later', 'now')
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM energy_history_queue WHERE source_key='source' AND report_month=%s", (live['report_month'],)).fetchone()
        assert row['status'] == 'running' and row['execution_owner'] == second[1]


def test_cutover_is_explicit_rejects_live_old_consumer_and_quarantines_unknown_source(postgres_state):
    from app.database.postgres import PostgresDatabase
    database, cfg, migrator = postgres_state
    queue = CoordinationRepository(database)
    owner = queue.acquire('worker', ttl=30)
    queue.enqueue('alarm', 'Cutter01', {}, key='legacy-alarm')
    with pytest.raises(RuntimeError, match='prepare-consumers'):
        WorkerProcess(cfg).start()
    with PostgresDatabase(migrator) as maintenance:
        with pytest.raises(MigrationError):
            prepare_consumers(maintenance)
        queue.release('worker', owner)
        assert prepare_consumers(maintenance)['unverified_source'] == 1
        assert prepare_consumers(maintenance)['unverified_source'] == 0
    queue.require_consumer_cutover()
    with database.connect() as connection:
        row = connection.execute("SELECT status,last_error FROM background_tasks WHERE dedup_key='legacy-alarm'").fetchone()
        assert row == {'status': 'blocked', 'last_error': 'SOURCE_UNVERIFIED'}


def test_unverified_source_task_never_uses_current_connection(postgres_state, monkeypatch):
    database, cfg, _ = postgres_state
    process = WorkerProcess(cfg)
    process.owner = process.repository.acquire(process.lease_name, ttl=30)
    line = process.runtime.database_runtime.snapshot().lines['Cutter01']
    monkeypatch.setattr(line.background, 'handle_background', lambda *args: pytest.fail('unverified source executed'))
    try:
        process.repository.enqueue('alarm', 'Cutter01', {}, key='unknown-source')
        with process.scope():
            task = process.repository.claim_task(process.owner, analysis=False)
            process._execute(task)
            process._flush_completions()
        assert process.repository.task_counts() == {'blocked': 1}
    finally:
        process.stop()


def test_diagnostics_identify_each_consumer_and_do_not_treat_identity_as_shared_config(postgres_state):
    from app.models.configuration_contracts import ConfigurationDiagnostics
    from app.services.application_runtime import build_runtime
    database, cfg, _ = postgres_state
    queue = CoordinationRepository(database)
    runtime = build_runtime(cfg, role='web')
    other = build_runtime(replace(cfg, consumer_instance_id='different-local-id'), role='worker')
    try:
        for name in ('scheduler', 'automation', 'worker:first', 'worker:second'):
            owner = queue.acquire(name, ttl=60)
            diagnostics = other.configuration_diagnostics if name == 'worker:second' else runtime.configuration_diagnostics
            queue.renew(name, owner, ttl=60, status={'report_alive':True, 'scheduler_alive':True,
                'automation_alive':True, 'email_alive':True, 'queue':{'available':True},
                'configuration':diagnostics.observed(owner)})
        status = WorkerStatusReader(database, expected_consumers=2).status()
        assert background_ready(status)
        report = runtime.configuration_diagnostics.report(status)
        ConfigurationDiagnostics.model_validate(report)
        assert report['consistent']
        assert set(report['processes']['consumers']) == {'first', 'second'}
        assert set(report['items'][0]['processes']['consumers']) == {'first', 'second'}
        assert not any(item['key'] == 'CONSUMER_INSTANCE_ID' for item in report['items'])
        with database.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())-1 WHERE name='worker:second'")
        lost = WorkerStatusReader(database, expected_consumers=2).status()
        assert lost['consumer_running_count'] == 1 and lost['consumer_lost_count'] == 1
        assert lost['roles']['worker']['state'] == 'degraded' and not background_ready(lost)
        diagnostics = runtime.configuration_diagnostics.report(lost)
        assert set(diagnostics['processes']['consumers']) == {'first'}
        assert diagnostics['processes']['worker']['state'] == 'degraded'
        assert diagnostics['errors'] == ['Consumer运行数量不足：期望2个，当前正常1个，请检查进程与心跳。']
        assert not diagnostics['consistent']
        recovered = runtime.configuration_diagnostics.report(WorkerStatusReader(database, expected_consumers=1).status())
        assert not recovered['errors'] and recovered['consistent']
    finally:
        for item in (runtime, other):
            item.database_runtime.close()
            item.database.close()


def test_p6_schema_upgrade_keeps_published_migrations_and_rejects_live_consumers(postgres_state):
    import psycopg
    from app.database.postgres import PostgresDatabase
    from app.database.postgres_migrations import apply_migrations
    database, cfg, migrator = postgres_state
    queue = CoordinationRepository(database)
    with PostgresDatabase(migrator) as maintenance:
        with maintenance.connect() as connection:
            connection.execute('DROP INDEX idx_process_lease_owner')
            connection.execute('ALTER TABLE background_tasks DROP COLUMN claim_token')
            connection.execute("DELETE FROM _postgres_schema_migrations WHERE version='003_consumer_scaling'")
            old = connection.execute('SELECT version,checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
        owner = queue.acquire('worker', ttl=30)
        with pytest.raises(psycopg.errors.RaiseException, match='Stop all Consumers'):
            apply_migrations(maintenance)
        queue.release('worker', owner)
        versions = apply_migrations(maintenance)
        assert versions[:2] == old and versions[2]['version'] == '003_consumer_scaling'
        assert apply_migrations(maintenance) == versions
        assert prepare_consumers(maintenance)['background'] == 0
    database.check_runtime()


def test_consumer_start_waits_for_the_same_maintenance_gate(postgres_state):
    from threading import Event
    from app.database.postgres import PostgresDatabase
    from app.database.postgres_migrations import MIGRATION_LOCK
    database, _, migrator = postgres_state
    entered = Event()
    queue = CoordinationRepository(database)
    def acquire():
        entered.set()
        return queue.acquire('worker:maintenance-race', ttl=30)
    with PostgresDatabase(migrator) as maintenance, ThreadPoolExecutor(1) as pool:
        with maintenance.connect() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(%s)', (MIGRATION_LOCK,))
            future = pool.submit(acquire)
            assert entered.wait(1)
            sleep(.05)
            assert not future.done()
        assert future.result(timeout=3)


def test_failed_report_file_replacement_cleans_own_temporary_file_and_keeps_other_owner(postgres_state, tmp_path, monkeypatch):
    from datetime import datetime
    from types import SimpleNamespace
    from app.services.report_service import ReportService
    database, cfg, _ = postgres_state
    first, second, _ = consumers(database)
    cfg.echarts_vendor_path.parent.mkdir(parents=True)
    cfg.echarts_vendor_path.write_text('/* isolated renderer fixture */')
    service = ReportService(cfg)
    monkeypatch.setattr(service.environment, 'get_template', lambda _: SimpleNamespace(render=lambda **kw:'报告结束'))
    arguments = dict(report_id='same-report', report={'meta':{'report_type':'equipment','report_date':'2026-11-04','title':'设备报表'}}, generated_at=datetime(2026,11,4))
    with scope(database, first):
        saved = service.render(**arguments)
    path = cfg.reports_dir / saved['relative_path']
    with monkeypatch.context() as patch:
        patch.setattr('app.services.report_service.os.replace', lambda *args: (_ for _ in ()).throw(OSError('interrupted replacement')))
        with scope(database, second), pytest.raises(OSError):
            service.render(**arguments)
    assert path.read_text() == '报告结束'
    assert first[1] in saved['relative_path']
    assert not list(cfg.reports_dir.rglob('*.tmp'))


@pytest.mark.parametrize('phase', ['execution', 'failure_write'])
def test_lost_report_ownership_never_marks_an_automation_run_failed(phase):
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo
    from app.workers.report_worker import ReportWorker
    failed = []
    def run(job, cancellation_check=None):
        if phase == 'execution':
            raise ExecutionLeaseLost('task moved to another owner')
        raise RuntimeError('generation result was uncertain')
    def update(*args, **kwargs):
        raise ExecutionLeaseLost('task no longer running under this owner')
    worker = ReportWorker(SimpleNamespace(), SimpleNamespace(update_status=update),
        SimpleNamespace(run=run), SimpleNamespace(report_failed=lambda *args:failed.append(args)))
    with pytest.raises(ExecutionLeaseLost):
        worker._process_report({'report_id':'moved-report','device_id':'Cutter01'}, ZoneInfo('Asia/Shanghai'))
    assert failed == []


@pytest.mark.parametrize('changes', [
    {'consumer_instance_id':'../unsafe'}, {'consumer_instance_id':'a'*65},
    {'consumer_operation_concurrency':0}, {'consumer_operation_concurrency':33},
    {'consumer_analysis_concurrency':0}, {'consumer_analysis_concurrency':33},
])
def test_consumer_identity_and_local_capacity_are_validated(changes):
    from app.config import Settings
    with pytest.raises(ValueError):
        Settings(**changes)


def test_concurrent_energy_initialization_is_idempotent_and_alarm_energy_tasks_have_sources(postgres_state, tmp_path):
    from app.scheduler import SchedulerProcess
    from tests.line_fixtures import configuration
    from tests.postgres_fixtures import state_settings
    cfg = state_settings(configuration(tmp_path, real=True, energy_api_enabled=True, alarm_monitor_enabled=True), postgres_state)
    workers = [WorkerProcess(replace(cfg, consumer_instance_id=f'initialize-{i}')) for i in range(2)]
    scheduler = SchedulerProcess(cfg)
    try:
        for worker in workers:
            worker.owner = worker.repository.acquire(worker.lease_name, ttl=30)
        def initialize(worker):
            with worker.scope():
                worker._initialize_lines()
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(initialize, workers))
        with postgres_state[0].connect() as connection:
            before = connection.execute('SELECT source_key,report_month FROM energy_history_queue ORDER BY source_key,report_month').fetchall()
        assert before and len({(row['source_key'], row['report_month']) for row in before}) == len(before)
        initialize(workers[0])
        with postgres_state[0].connect() as connection:
            after = connection.execute('SELECT source_key,report_month FROM energy_history_queue ORDER BY source_key,report_month').fetchall()
        assert before == after
        scheduler.owner = scheduler.repository.acquire('scheduler', ttl=30)
        with scheduler.scope():
            scheduler._schedule()
        import json
        with postgres_state[0].connect() as connection:
            tasks = connection.execute("SELECT kind,device_id,payload FROM background_tasks WHERE kind IN ('alarm','energy_sync')").fetchall()
        assert {task['kind'] for task in tasks} == {'alarm', 'energy_sync'}
        lines = scheduler.runtime.database_runtime.snapshot().lines
        assert all(json.loads(task['payload'])['source_fingerprint'] == lines[task['device_id']].settings.source_fingerprint for task in tasks)
    finally:
        for worker in workers:
            worker.stop()
        scheduler.stop()


@pytest.mark.parametrize('kind', ['background', 'report', 'energy'])
def test_uncommitted_renewal_cannot_be_mistaken_for_an_abandoned_owner(postgres_state, kind):
    from threading import Event
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    first, recoverer, unused, expired = consumers(database, 4)
    metadata, energy = MetadataRepository(database), EnergyReportRepository(database)
    if kind == 'background':
        for line in ('Cutter01', 'Cutter02'):
            queue.enqueue('current_refresh', line, {'source_fingerprint':line})
        old = queue.claim_task(first[1], analysis=False)
        abandoned = queue.claim_task(expired[1], analysis=False)
        table, field, old_key, abandoned_key = 'background_tasks', 'task_id', old['task_id'], abandoned['task_id']
        old_status, recovered_status = 'running', 'deferred'
    elif kind == 'report':
        for report_id in ('renew-a', 'renew-b'):
            create_report(metadata, report_id)
        with scope(database, first):
            old = metadata.claim_next_job('start')
        with scope(database, expired):
            abandoned = metadata.claim_next_job('start')
        table, field, old_key, abandoned_key = 'report_jobs', 'report_id', old['report_id'], abandoned['report_id']
        old_status, recovered_status = 'querying', 'pending'
    else:
        energy.enqueue_months('renew-source', ['2026-09-01', '2026-10-01'], 'now')
        with scope(database, first):
            old = energy.claim_history_month('renew-source', 'now')
        with scope(database, expired):
            abandoned = energy.claim_history_month('renew-source', 'now')
        table, field, old_key, abandoned_key = 'energy_history_queue', 'report_month', old['report_month'], abandoned['report_month']
        old_status, recovered_status = 'running', 'pending'
    expire(database, expired)
    with database.connect() as connection:
        connection.execute('UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())+.4 WHERE name=%s', (first[0],))
    entered, release = Event(), Event()
    def renewing():
        with database.connect() as connection:
            connection.execute('SELECT name FROM process_leases WHERE name=%s FOR UPDATE', (first[0],))
            connection.execute('UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())+60 WHERE name=%s', (first[0],))
            entered.set()
            assert release.wait(8)
    def recover():
        with scope(database, recoverer):
            if kind == 'background':
                queue.recover_tasks(recoverer[1])
            elif kind == 'report':
                metadata.recover_interrupted_jobs()
            else:
                energy.recover_history_queue('renew-source', 'now')
    def status(key):
        with database.connect() as connection:
            return connection.execute(f'SELECT status FROM {table} WHERE {field}=%s', (key,)).fetchone()['status']
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(renewing)
        try:
            assert entered.wait(2)
            sleep(.45)
            recover()
            assert status(old_key) == old_status
            assert status(abandoned_key) == recovered_status, 'a locked renewal must not block another abandoned owner'
        finally:
            release.set()
        future.result(timeout=3)
    recover()
    assert status(old_key) == old_status


def test_stopped_consumer_does_not_keep_the_live_cluster_stalled(postgres_state):
    from app.services.worker_status import role_health
    database, cfg, _ = postgres_state
    queue = CoordinationRepository(database)
    owners = {}
    for name in ('scheduler', 'automation', 'worker:healthy', 'worker:stalled'):
        owners[name] = queue.acquire(name, ttl=30)
        queue.renew(name, owners[name], ttl=30, status={'scheduler_alive':True, 'automation_alive':True,
            'email_alive':True, 'report_alive':True, 'queue':{'available':True},
            'stalled_workers':['report'] if name == 'worker:stalled' else []})
    assert not background_ready(WorkerStatusReader(database).status())
    assert role_health(cfg, 'worker:healthy') == 0
    assert role_health(cfg, 'worker:stalled') == 1
    queue.release('worker:stalled', owners['worker:stalled'])
    status = WorkerStatusReader(database).status()
    assert background_ready(status) and status['consumer_running_count'] == 1
    assert status['stalled_workers'] == []


@pytest.mark.parametrize('kind', ['background', 'report', 'energy'])
def test_old_instance_records_are_kept_until_their_running_tasks_are_recovered(postgres_state, kind):
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    old, live, unreferenced = consumers(database)
    metadata, energy = MetadataRepository(database), EnergyReportRepository(database)
    if kind == 'background':
        queue.enqueue('current_refresh', 'Cutter01', {'source_fingerprint':'source'})
        assert queue.claim_task(old[1], analysis=False)
    elif kind == 'report':
        create_report(metadata, 'retained-owner-report')
        with scope(database, old):
            assert metadata.claim_next_job('start')
    else:
        energy.enqueue_months('retained-source', ['2026-09-01'], 'now')
        with scope(database, old):
            assert energy.claim_history_month('retained-source', 'now')
    with database.connect() as connection:
        connection.execute("""UPDATE process_leases SET heartbeat_at=EXTRACT(EPOCH FROM clock_timestamp())-31*86400,
            expires_at=EXTRACT(EPOCH FROM clock_timestamp())-31*86400 WHERE name=ANY(%s)""", ([old[0], unreferenced[0]],))
    with scope(database, live):
        queue.cleanup_completed()
    assert queue.worker_status(old[0])['state'] == 'lost'
    assert queue.worker_status(unreferenced[0])['state'] == 'missing'
    with scope(database, live):
        if kind == 'background':
            queue.recover_tasks(live[1])
        elif kind == 'report':
            metadata.recover_interrupted_jobs()
        else:
            energy.recover_history_queue('retained-source', 'now')
        queue.cleanup_completed()
    assert queue.worker_status(old[0])['state'] == 'missing'


def test_snapshot_round_claim_token_rejects_old_completion_even_with_same_owner_and_attempt(postgres_state):
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    consumer = consumers(database, 1)[0]
    payload = {'report_type':'daily', 'query':{}, 'source_fingerprint':'source'}
    assert queue.request_snapshot_refresh('welding', payload)
    old = queue.claim_task(consumer[1], analysis=False)
    assert queue.finish_task(old, consumer[1])
    assert queue.request_snapshot_refresh('welding', payload)
    current = queue.claim_task(consumer[1], analysis=False)
    assert current['task_id'] == old['task_id'] and current['attempts'] == old['attempts'] == 1
    assert current['claim_token'] != old['claim_token']
    assert queue.finish_task(old, consumer[1]) is None
    assert queue.task_counts() == {'running':1}
    assert queue.finish_task(current, consumer[1])


def test_blocked_source_snapshots_do_not_fill_active_capacity_and_current_plan_can_resume(postgres_state):
    from time import time
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    consumer = consumers(database, 1)[0]
    old = {'report_type':'daily', 'query':{}, 'source_fingerprint':'old-source'}
    assert queue.request_snapshot_refresh('welding', old)
    task = queue.claim_task(consumer[1], analysis=False)
    assert queue.finish_task(task, consumer[1], 'SOURCE_UNVERIFIED')
    for index in range(64):
        queue.enqueue('report_snapshot_refresh', 'welding', {**old, 'query':{'page':index}})
    with database.connect() as connection:
        connection.execute("UPDATE background_tasks SET status='blocked',last_error='SOURCE_UNVERIFIED',updated_at=%s", (time()-60,))
    assert queue.request_snapshot_refresh('welding', {**old, 'source_fingerprint':'current-source'})
    assert queue.snapshot_refresh_due('welding', old, 30) <= time()
    current_plan = {**old, 'schema_version':'current'}
    assert queue.request_snapshot_refresh('welding', current_plan, interval=30)
    with database.connect() as connection:
        row = connection.execute('SELECT status,payload FROM background_tasks WHERE task_id=%s', (task['task_id'],)).fetchone()
    import json
    assert row['status'] == 'pending' and json.loads(row['payload']) == current_plan


def test_busy_scale_down_keeps_lease_until_expiry_and_fences_the_late_thread(postgres_state, monkeypatch):
    from threading import Event
    database, cfg, _ = postgres_state
    worker = WorkerProcess(replace(cfg, consumer_instance_id='draining'))
    queue = CoordinationRepository(database)
    worker.owner = queue.acquire(worker.lease_name, ttl=30)
    entered, release = Event(), Event()
    def late_write(task):
        entered.set()
        assert release.wait(15)
        with database.connect() as connection:
            connection.execute("INSERT INTO llm_waiters VALUES('stale-draining-thread',0)")
    monkeypatch.setattr(worker, '_handle', late_write)
    queue.enqueue('current_refresh', 'Cutter01', {'source_fingerprint':'source'}, key='draining-task')
    task = queue.claim_task(worker.owner, analysis=False)
    future = None
    try:
        with worker.scope():
            future = worker.operations.submit(worker._execute, task)
        assert entered.wait(2)
        worker.stop()
        with database.connect() as connection:
            lease = connection.execute('SELECT owner,expires_at FROM process_leases WHERE name=%s', (worker.lease_name,)).fetchone()
            assert lease['owner'] == worker.owner and lease['expires_at'] > 0, 'unfinished execution cannot release its lease'
        survivor = queue.acquire('worker:survivor', ttl=30)
        queue.recover_tasks(survivor)
        assert queue.task_counts() == {'running':1}
        expire(database, (worker.lease_name, worker.owner))
        queue.recover_tasks(survivor)
        queue.admit_deferred()
        current = queue.claim_task(survivor, analysis=False)
        assert current['task_id'] == task['task_id'] and current['claim_token'] != task['claim_token']
        release.set()
        future.result(timeout=3)
        with database.connect() as connection:
            assert connection.execute("SELECT 1 FROM llm_waiters WHERE waiter_id='stale-draining-thread'").fetchone() is None
        assert queue.finish_task(current, survivor)
    finally:
        release.set()
        if future is not None:
            future.result(timeout=3)
        worker.stop()


def test_replaced_consumer_history_does_not_reduce_current_capacity(postgres_state):
    database, _, _ = postgres_state
    queue = CoordinationRepository(database)
    for name in ('scheduler', 'automation', 'worker:old', 'worker:current-1', 'worker:current-2'):
        owner = queue.acquire(name, ttl=60)
        queue.renew(name, owner, ttl=60, status={'report_alive': True, 'scheduler_alive': True,
            'automation_alive': True, 'email_alive': True, 'queue': {'available': True}})
    with database.connect() as connection:
        connection.execute("UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())-1 WHERE name='worker:old'")
    status = WorkerStatusReader(database).status()
    assert status['consumer_running_count'] == 2 and status['consumer_lost_count'] == 1
    assert background_ready(status), 'a replaced historical instance must not keep healthy Consumers degraded'

    assert status['consumer_expected_count'] == 1 and status['consumer_capacity_shortfall'] == 0
    assert {item['instance_id']: item['state'] for item in status['consumer_instances']} == {
        'old': 'lost', 'current-1': 'running', 'current-2': 'running',
    }
    from app.services.application_runtime import build_runtime
    runtime = build_runtime(replace(postgres_state[1], consumer_replicas=3), role='web')
    try:
        limited = runtime.worker_status_reader.status()
        assert not background_ready(limited) and limited['roles']['worker']['state'] == 'degraded'
        assert limited['consumer_expected_count'] == 3 and limited['consumer_capacity_shortfall'] == 1
        assert limited['scheduler_alive'] and limited['automation_alive'] and limited['email_alive']
        owner = queue.acquire('worker:current-3', ttl=60)
        queue.renew('worker:current-3', owner, ttl=60, status={'report_alive': True, 'queue': {'available': True}})
        assert background_ready(WorkerStatusReader(database, expected_consumers=3).status())
        with database.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=EXTRACT(EPOCH FROM clock_timestamp())-1 WHERE name LIKE 'worker:current-%'")
        empty = WorkerStatusReader(database).status()
        assert not background_ready(empty) and empty['roles']['worker']['state'] == 'unavailable'
        assert empty['consumer_running_count'] == 0 and empty['consumer_lost_count'] == 4
    finally:
        runtime.database_runtime.close()
        runtime.database.close()


def test_consumer_expected_capacity_must_be_positive(tmp_path):
    from tests.line_fixtures import configuration
    with pytest.raises(ValueError, match='CONSUMER_REPLICAS'):
        configuration(tmp_path, consumer_replicas=0)
