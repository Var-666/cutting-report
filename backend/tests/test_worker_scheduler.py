from tests.line_fixtures import as_line_services, runtime_line
from collections import Counter
from dataclasses import replace
from datetime import datetime
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo
import sqlite3

import pytest

from app.database.postgres import ExecutionLeaseLost
from tests.postgres_fixtures import state_settings
from app.services.application_runtime import build_runtime
from app.worker import WorkerProcess
from app.scheduler import SchedulerProcess
from test_web_worker_processes import configuration


def test_periodic_rounds_are_durable_device_scoped_and_do_not_duplicate_reconciliation(postgres_state, tmp_path, monkeypatch):
    from app.database import coordination
    now = [coordination.time()]
    monkeypatch.setattr(coordination, "time", lambda: now[0])
    monkeypatch.setattr('app.scheduler.time', lambda: now[0])
    monkeypatch.setattr('app.scheduler.monotonic', lambda: now[0])
    from tests.line_fixtures import configuration as line_configuration
    configured = replace(state_settings(line_configuration(tmp_path, real=True),postgres_state), llm_enabled=True,
                         leadership_report_reconcile_hour=0)
    process = SchedulerProcess(configured)
    process._schedule()
    with process.repository.connect() as connection:
        rows = [dict(row) for row in connection.execute("SELECT kind,device_id,payload FROM background_tasks")]
    for device_id, line in process.runtime.database_runtime.snapshot().lines.items():
        if not line.config.enabled or line.background is None:
            continue
        assert Counter(row["kind"] for row in rows if row["device_id"] == device_id) == {
            "current_refresh": 1, "leadership_refresh": 3,
        }
        current = process.runtime.database_runtime.snapshot().lines[device_id].service.current_report_refresh_worker
        from datetime import date
        current._schedule_analysis("workshop", (date(2026, 10, 24),))
        current._schedule_analysis("workshop", (date(2026, 10, 24),))
    assert process.repository.task_counts()["pending"] == len(rows) + 4
    replacement = SchedulerProcess(configured)
    replacement._schedule()
    assert replacement.repository.task_counts() == process.repository.task_counts()
    assert replacement.repository.task_counts()["pending"] == len(rows) + 4
    now[0] += 301
    replacement._schedule()
    with process.repository.connect() as connection:
        rounds = connection.execute("SELECT device_id, COUNT(*) AS count FROM background_tasks WHERE kind='current_refresh' GROUP BY device_id").fetchall()
    assert {row["device_id"]:row["count"] for row in rounds} == {key: 2 for key, line in process.runtime.database_runtime.snapshot().lines.items() if line.config.enabled and line.background is not None}
    assert not hasattr(process, "operations") and not hasattr(process, "analysis")



def test_task_failure_retries_after_backoff_and_stale_owner_cannot_finish(postgres_state, tmp_path, monkeypatch):
    process = WorkerProcess(state_settings(configuration(tmp_path),postgres_state))
    repository = process.repository
    process.owner = repository.acquire("worker:test-consumer", ttl=30)
    repository.enqueue("analysis_workshop", "Cutter02", {"date": "2026-10-24"})
    task = repository.claim_task(process.owner, analysis=True)
    monkeypatch.setattr(process, "_handle", lambda _: (_ for _ in ()).throw(TimeoutError("model fixture")))
    with process.scope():
        process._execute(task)
        process._flush_completions()
    assert repository.task_counts() == {"deferred": 1}
    repository.admit_deferred()
    assert repository.claim_task(process.owner, analysis=True) is None
    with repository.connect() as connection:
        row = connection.execute("SELECT * FROM background_tasks").fetchone()
        assert row["last_error"] == "TimeoutError"
        assert row["next_attempt"] > row["updated_at"]
        connection.execute("UPDATE background_tasks SET next_attempt=0")
    repository.admit_deferred()
    retry = repository.claim_task(process.owner, analysis=True)
    assert retry["attempts"] == 2 and retry["device_id"] == "Cutter02"
    old = process.owner
    with repository.connect() as connection:
        connection.execute("UPDATE process_leases SET expires_at=0")
    process.owner = repository.acquire("worker:test-consumer", ttl=30)
    with process.scope():
        repository.recover_tasks(process.owner)
        repository.admit_deferred()
        current = repository.claim_task(process.owner, analysis=True)
        assert current["task_id"] == retry["task_id"]
        with pytest.raises(ExecutionLeaseLost):
            repository.finish_task(retry, old)
        repository.finish_task(current, process.owner)
    assert repository.task_counts() == {"completed": 1}


@pytest.mark.parametrize('payload', [{}, {'report_type': 'dashboard', 'query': None}])
def test_malformed_snapshot_task_is_recorded_as_failed_without_breaking_completion(postgres_state, tmp_path, payload, caplog):
    from tests.line_fixtures import configuration as line_configuration
    process = WorkerProcess(state_settings(line_configuration(tmp_path, welding=True),postgres_state))
    queue = process.repository
    process.owner = queue.acquire('worker:test-consumer', ttl=30)
    queue.enqueue('report_snapshot_refresh', 'welding', payload)
    try:
        task = queue.claim_task(process.owner, analysis=False)
        with caplog.at_level('INFO'), process.scope():
            process._execute(task)
            process._flush_completions()
        assert queue.task_counts() == {'blocked': 1}
        assert 'outcome=failed elapsed_ms=' in caplog.text
        with queue.connect() as connection:
            assert connection.execute('SELECT last_error FROM background_tasks').fetchone()['last_error']
    finally:
        process.stop()


def test_runtime_startup_preserves_existing_business_state(tmp_path, postgres_state):
    configured = configuration(tmp_path, postgres_state)
    legacy = build_runtime(configured, role='web')
    legacy.metadata.create_job(report_id='legacy',report_type='equipment',report_date='2026-11-04',
        project_code=None,ship_no=None,request_id='legacy',created_at='2026-11-04T08:00:00+08:00',device_id='Cutter01')
    legacy.metadata.update_status('legacy','analyzing','saved state')
    before = legacy.metadata.get_job('legacy')
    legacy.database_runtime.close()
    legacy.database.close()
    upgraded = build_runtime(configured, role='worker')
    try:
        assert upgraded.metadata.get_job('legacy') == before
        assert not list(configured.data_dir.rglob('*.db'))
    finally:
        upgraded.database_runtime.close()
        upgraded.database.close()


@pytest.mark.parametrize(("database_available", "worker_state", "expected_status"), [
    (True, "stopped", 200), (True, "lost", 200), (False, "unavailable", 503),
])
def test_web_health_distinguishes_worker_failure_from_database_failure(database_available, worker_state, expected_status):
    import anyio
    from types import SimpleNamespace
    from app.main import health
    status = {"state": worker_state, "report_alive": False, "database_available": database_available}
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        worker_status_reader=SimpleNamespace(status=lambda: status))), state=SimpleNamespace(request_id="health-test"))
    response = anyio.run(health, request)
    if expected_status == 503:
        assert response.status_code == expected_status
    else:
        assert response["success"]
        assert response["data"]["background_ready"] is False
        assert response["data"]["background_status"] == worker_state


@pytest.fixture
def clock(monkeypatch):
    value = [datetime(2026, 11, 3, 12, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp(), 0.0]
    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(value[0], tz)
    monkeypatch.setattr('app.scheduler.datetime', ClockDateTime)
    monkeypatch.setattr('app.services.welding_service.datetime', ClockDateTime)
    monkeypatch.setattr('app.scheduler.time', lambda: value[0])
    monkeypatch.setattr('app.scheduler.monotonic', lambda: value[1])
    monkeypatch.setattr('app.worker.monotonic', lambda: value[1])
    monkeypatch.setattr('app.worker.time', lambda: value[0])
    monkeypatch.setattr('app.database.coordination.time', lambda: value[0])
    return value


def trace_sql(database, monkeypatch):
    from psycopg import Cursor
    statements = []
    class ObservedCursor(Cursor):
        def execute(self, query, *args, **kwargs):
            statements.append(query if isinstance(query,str) else query.as_string(self.connection))
            return super().execute(query,*args,**kwargs)
    original = database.connect
    @contextmanager
    def connect(**options):
        with original(**options) as connection:
            before = connection.cursor_factory
            connection.cursor_factory = ObservedCursor
            try:
                yield connection
            finally:
                connection.cursor_factory = before
    monkeypatch.setattr(database, 'connect', connect)
    return statements


def test_idle_schedule_stays_in_memory_and_due_rounds_keep_the_same_cadence(postgres_state, tmp_path, monkeypatch, clock):
    from tests.line_fixtures import configuration
    process = SchedulerProcess(state_settings(configuration(tmp_path, real=True, welding=True, alarm_monitor_enabled=True,
                                          leadership_report_reconcile_hour=0),postgres_state))
    calls = {}
    for name in ('schedule', 'next_due', 'enqueue', 'request_snapshot_refresh', 'snapshot_refresh_due'):
        calls[name] = Mock(wraps=getattr(process.repository, name))
        monkeypatch.setattr(process.repository, name, calls[name])
    bootstraps = []
    for line in process.runtime.database_runtime.snapshot().lines.values():
        if line.background:
            probe = Mock(return_value=True)
            monkeypatch.setattr(line.service.leadership_report_service, 'needs_bootstrap', probe)
            bootstraps.append(probe)
    process._schedule()
    assert calls['schedule'].call_count == 8
    assert calls['enqueue'].call_count == calls['request_snapshot_refresh'].call_count == 4
    with process.repository.connect() as connection:
        connection.execute("UPDATE background_tasks SET status='completed'")
    before = {key: value.call_count for key, value in calls.items()}
    statements = trace_sql(process.repository.database, monkeypatch)
    for _ in range(299):
        clock[0] += 1
        clock[1] += 1
        process._schedule()
    assert {key: value.call_count for key, value in calls.items()} == before
    assert not statements  # Includes bootstrap and queue checks, not just write transactions.
    clock[0] += 1
    clock[1] += 1
    process._schedule()
    assert calls['schedule'].call_count == 14
    assert calls['request_snapshot_refresh'].call_count == 8
    assert calls['enqueue'].call_count == 4
    assert all(probe.call_count == 2 for probe in bootstraps)



@pytest.mark.parametrize('restart_after', [150, 301])
def test_restart_recovers_persisted_deadline_and_pending_reconciliation(postgres_state, tmp_path, clock, restart_after):
    from tests.line_fixtures import configuration
    cfg = state_settings(configuration(tmp_path, real=True, leadership_report_reconcile_hour=0),postgres_state)
    first = SchedulerProcess(cfg)
    first._schedule()
    clock[0] += restart_after
    clock[1] += restart_after
    replacement = SchedulerProcess(cfg)
    replacement._schedule()
    with replacement.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='current_refresh'").fetchone()['count'] == (2 if restart_after < 300 else 4)
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE dedup_key LIKE 'reconcile:%'").fetchone()['count'] == 4
        assert connection.execute("SELECT next_due FROM background_schedules WHERE schedule_key='current_refresh:Cutter01'").fetchone()['next_due'] == clock[0] + (300 - restart_after if restart_after < 300 else 300)
    if restart_after < 300:
        clock[0] += 300 - restart_after
        clock[1] += 300 - restart_after
        replacement._schedule()
        with replacement.repository.connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='current_refresh'").fetchone()['count'] == 4
    counts = replacement.repository.task_counts()
    SchedulerProcess(cfg)._schedule()
    assert replacement.repository.task_counts() == counts



@pytest.mark.parametrize('failure', ['exception', 'not-persisted'])
def test_enqueue_failure_rolls_back_due_and_leaves_a_retry(postgres_state, tmp_path, monkeypatch, clock, failure):
    from tests.line_fixtures import configuration
    from app.database.coordination import CoordinationRepository
    process = SchedulerProcess(state_settings(configuration(tmp_path, real=True),postgres_state))
    original = CoordinationRepository.enqueue
    def fail(*args, **kwargs):
        if failure == 'exception':
            raise sqlite3.OperationalError('test write failure')
        return False
    monkeypatch.setattr(CoordinationRepository, 'enqueue', fail)
    with pytest.raises((sqlite3.OperationalError, RuntimeError)):
        process._schedule()
    with process.repository.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM background_schedules').fetchone()['count'] == 0
        assert connection.execute('SELECT COUNT(*) FROM background_tasks').fetchone()['count'] == 0
    monkeypatch.setattr(CoordinationRepository, 'enqueue', original)
    process._schedule()
    assert process.repository.task_counts()['pending'] >= 4



def test_daily_reconciliation_tracks_each_month_retries_and_resets_on_the_next_day(postgres_state, tmp_path, monkeypatch, clock):
    from tests.line_fixtures import configuration
    process = SchedulerProcess(state_settings(configuration(tmp_path, real=True, leadership_report_reconcile_hour=13),postgres_state))
    original = process.repository.enqueue
    attempts = Counter()
    def enqueue(kind, line, payload, *, key):
        attempts[key] += 1
        if line == 'Cutter01' and payload['date'] == '2026-10-01' and '2026-11-03' in key and attempts[key] == 1:
            return False
        return original(kind, line, payload, key=key)
    monkeypatch.setattr(process.repository, 'enqueue', enqueue)
    process._schedule()
    assert not attempts
    clock[0] += 3600
    clock[1] += 3600
    process._schedule()
    assert sum(attempts.values()) == 4
    process._schedule()
    assert sum(attempts.values()) == 5
    for _ in range(10):
        process._schedule()
    assert sum(attempts.values()) == 5
    assert len(process._reconciled) == 4
    clock[0] += 86400
    clock[1] += 86400
    process._schedule()
    assert sum(attempts.values()) == 9
    assert len(process._reconciled) == 4
    assert all('2026-11-04' in key for key in process._reconciled)



def test_interval_hotload_recalculates_from_the_last_persisted_round_and_survives_restart(postgres_state, tmp_path, clock):
    from tests.line_fixtures import configuration
    from app.services.database_runtime import database_context
    from app.services.runtime_settings_store import write_runtime_env
    cfg = state_settings(configuration(tmp_path, real=True, welding=True),postgres_state)
    process = SchedulerProcess(cfg)
    process._schedule()
    with process.repository.connect() as connection:
        connection.execute("UPDATE background_tasks SET status='completed'")
    start = clock[0]
    clock[0] += 20
    clock[1] += 20
    write_runtime_env(cfg.runtime_settings_env_path, {'REPORT_BACKGROUND_REFRESH_SECONDS': 60})
    process._schedule()
    context = database_context(process.runtime)
    assert context.settings.report_background_refresh_seconds == 60
    assert all(line.settings.report_background_refresh_seconds == 60 for line in context.lines.values())
    clock[0] += 40
    clock[1] += 40
    process._schedule()
    with process.repository.connect() as connection:
        assert tuple(connection.execute("SELECT next_due, interval FROM background_schedules WHERE schedule_key='current_refresh:Cutter01'").fetchone().values()) == (start + 120, 60)
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='current_refresh'").fetchone()['count'] == 4
    write_runtime_env(cfg.runtime_settings_env_path, {'REPORT_BACKGROUND_REFRESH_SECONDS': 600})
    process._schedule()
    replacement = SchedulerProcess(cfg)
    replacement._schedule()
    assert replacement._next_due['current_refresh:Cutter01'].deadline == 660
    clock[0] += 600
    clock[1] += 600
    replacement._schedule()
    with process.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='current_refresh'").fetchone()['count'] == 6
    before = database_context(replacement.runtime)
    for invalid in ('-1', 'not-an-integer'):
        write_runtime_env(cfg.runtime_settings_env_path, {'REPORT_BACKGROUND_REFRESH_SECONDS': invalid})
        replacement._schedule()
        assert database_context(replacement.runtime) is before
        assert replacement.runtime.database_runtime.inspect()['error']



def test_empty_and_backing_off_queues_do_not_acquire_a_write_transaction(postgres_state, tmp_path, monkeypatch, clock):
    from app.database.coordination import CoordinationRepository
    repository = CoordinationRepository(postgres_state[0])
    owner = repository.acquire('worker:test-consumer', ttl=300)
    statements = trace_sql(repository.database, monkeypatch)
    repository.admit_deferred()
    assert repository.claim_task(owner, analysis=False) is None
    assert repository.claim_task(owner, analysis=True) is None
    assert 'BEGIN IMMEDIATE' not in statements
    repository.enqueue('current_refresh', 'Cutter01', {}, capacity=0)
    statements.clear()
    repository.admit_deferred(capacity=0)
    assert 'BEGIN IMMEDIATE' not in statements
    repository.admit_deferred()
    task = repository.claim_task(owner, analysis=False)
    repository.finish_task(task, owner, 'test failure')
    statements.clear()
    clock[0] += 29
    clock[1] += 29
    repository.admit_deferred()
    assert repository.claim_task(owner, analysis=False) is None
    assert 'BEGIN IMMEDIATE' not in statements
    clock[0] += 1
    clock[1] += 1
    repository.admit_deferred()
    assert repository.claim_task(owner, analysis=False)['attempts'] == 2


def test_web_submissions_are_polled_each_tick_and_capacity_release_admits_deferred_early(postgres_state, tmp_path, monkeypatch, clock):
    from tests.line_fixtures import configuration
    from app.database.coordination import CoordinationRepository
    process = WorkerProcess(state_settings(configuration(tmp_path),postgres_state))
    process.owner = process.repository.acquire('worker:test-consumer', ttl=30)
    received = []
    process.operations = process.analysis = SimpleNamespace(available=lambda: True, submit=lambda fn, task: received.append(task))
    admit = Mock(wraps=process.repository.admit_deferred)
    monkeypatch.setattr(process.repository, 'admit_deferred', admit)
    process.tick()
    assert admit.call_count == 1
    web = CoordinationRepository(process.repository.database)
    web.enqueue('current_refresh', 'Cutter01', {}, capacity=1)
    web.enqueue('current_refresh', 'Cutter02', {}, capacity=1)
    clock[0] += 1
    clock[1] += 1
    process.tick()
    assert admit.call_count == 1
    assert len(received) == 1
    clock[0] += 1
    clock[1] += 1
    process.tick()
    assert admit.call_count == 2
    assert [task['device_id'] for task in received] == ['Cutter01', 'Cutter02']
    process._completed.append((received[0], 'test failure'))
    clock[0] += 1
    clock[1] += 1
    process.tick()
    assert len(received) == 2
    assert process.repository.task_counts() == {'deferred': 1, 'running': 1}
    for _ in range(5):
        clock[0] += 1
        clock[1] += 1
        process.tick()
    assert admit.call_count == 4


def test_hotadded_shared_source_lines_plan_changes_and_retirement_keep_state_bounded(postgres_state, tmp_path, monkeypatch, clock):
    from tests.line_fixtures import configuration
    from app.services.database_runtime import database_context
    from app.services.production_reports import report_providers
    from app.models.database_sources import DatabaseSourceRequest
    process = SchedulerProcess(state_settings(configuration(tmp_path, real=True, welding=True, leadership_report_reconcile_hour=0),postgres_state))
    process._schedule()
    untouched = process._next_due['current_refresh:Cutter01']
    context = database_context(process.runtime)
    catalog = context.catalog
    third = catalog.get('Cutter02').model_copy(update={'id': 'Cutter03', 'device_id': 'physical-3', 'machine_no': 'Cutter03'})
    first_weld = catalog.get('welding').model_copy(update={'device_nos': (0,)})
    second_weld = first_weld.model_copy(update={'id': 'weld-2', 'device_nos': (2,)})
    catalog = catalog.model_copy(update={'lines': (*catalog.lines[:-1], third, first_weld, second_weld)})
    process.runtime.database_runtime.save_lines(catalog)
    process._schedule()
    assert process._next_due['current_refresh:Cutter01'] is untouched
    assert len(process._next_due) == 16
    with process.repository.connect() as connection:
        assert {row['device_id'] for row in connection.execute('SELECT DISTINCT device_id FROM background_tasks')} >= {'Cutter03', 'weld-2'}
    retired = catalog.model_copy(update={'lines': tuple(line.model_copy(update={'enabled': False}) if line.id == 'weld-2' else line for line in catalog.lines)})
    process.runtime.database_runtime.save_lines(retired)
    request = Mock(wraps=process.repository.request_snapshot_refresh)
    monkeypatch.setattr(process.repository, 'request_snapshot_refresh', request)
    process._schedule()
    assert len(process._next_due) == 12
    assert request.call_count == 0
    process.runtime.database_runtime.save_lines(catalog)
    process._schedule()
    assert len(process._next_due) == 16
    assert request.call_count == 0  # Re-enabling finds already persisted work.
    old_keys = {key for key in process._next_due if key.startswith('report_snapshot:')}
    source = database_context(process.runtime).database_sources.get('welding-production')
    process.runtime.database_runtime.save_source(DatabaseSourceRequest.model_validate({**source.model_dump(), 'host': 'new.test'}))
    process._schedule()
    assert request.call_count == 8
    assert not old_keys.intersection(process._next_due)
    assert process._next_due['current_refresh:Cutter01'] is untouched
    provider = database_context(process.runtime).lines['weld-2'].report_provider
    original_plan = provider.refresh_plan
    monkeypatch.setattr(provider, 'refresh_plan', lambda: original_plan()[:-1])
    for _ in range(5):
        clock[0] += 86400
        clock[1] += 86400
        process._schedule()
        assert len(process._next_due) == 15
        assert len(process._reconciled) == 6
        expected = {process.repository.snapshot_task_key(line_id, payload)
                    for line_id, item in report_providers(database_context(process.runtime)) for payload, _ in item.refresh_plan()}
        assert {key for key in process._next_due if key.startswith('report_snapshot:')} == expected



def test_snapshot_plan_parameters_and_intervals_remain_independent_and_capacity_rejection_retries(postgres_state, tmp_path, monkeypatch, clock):
    from tests.line_fixtures import configuration
    from app.services.database_runtime import database_context
    process = SchedulerProcess(state_settings(configuration(tmp_path, welding=True),postgres_state))
    provider = database_context(process.runtime).lines['welding'].report_provider
    payload = provider.refresh_plan()[0][0]
    other = {**payload, 'query': {**payload['query'], 'page': 2}}
    monkeypatch.setattr(provider, 'refresh_plan', lambda: [(payload, 30), (other, 60)])
    original = process.repository.request_snapshot_refresh
    attempts = []
    def reject_once(line_id, selected, *, interval):
        attempts.append(selected)
        return False if len(attempts) == 1 else original(line_id, selected, interval=interval)
    monkeypatch.setattr(process.repository, 'request_snapshot_refresh', reject_once)
    process._schedule()
    assert len(attempts) == 2
    process._schedule()
    assert len(attempts) == 3 and attempts[-1] == payload
    with process.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='report_snapshot_refresh'").fetchone()['count'] == 2
        connection.execute("UPDATE background_tasks SET status='completed' WHERE kind='report_snapshot_refresh'")
    clock[0] += 30
    clock[1] += 30
    process._schedule()
    assert len(attempts) == 4 and attempts[-1] == payload
    clock[0] += 30
    clock[1] += 30
    process._schedule()
    assert len(attempts) == 5 and attempts[-1] == other



@pytest.mark.parametrize('jump', [-3600, 3600])
def test_wall_clock_steps_rebase_cached_deadlines_on_persisted_progress(postgres_state, tmp_path, clock, jump):
    from tests.line_fixtures import configuration
    process = SchedulerProcess(state_settings(configuration(tmp_path, real=True),postgres_state))
    process._schedule()
    clock[0] += jump
    clock[1] += 1
    process._schedule()
    with process.repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='current_refresh'").fetchone()['count'] == (2 if jump < 0 else 4)
    assert process._next_due['current_refresh:Cutter01'].deadline == (3901 if jump < 0 else 301)



def test_imported_schedule_adopts_interval_without_changing_deadline(tmp_path, monkeypatch, clock, postgres_state):
    from app.database.coordination import CoordinationRepository
    database = postgres_state[0]
    due = clock[0] + 150
    with database.connect() as connection:
        connection.execute('INSERT INTO background_schedules(schedule_key,next_due) VALUES (%s,%s)',('current_refresh:Cutter01',due))
    repository = CoordinationRepository(database)
    assert repository.next_due('current_refresh:Cutter01',300) == due
    with database.connect() as connection:
        assert connection.execute('SELECT interval FROM background_schedules').fetchone()['interval'] == 300
    assert repository.next_due('current_refresh:Cutter01',300) == due
    assert repository.next_due('current_refresh:Cutter01',60) == due - 240


def test_snapshot_completion_updates_the_deadline_without_shared_memory(postgres_state, tmp_path, monkeypatch, clock):
    from tests.line_fixtures import configuration
    from app.services.database_runtime import database_context
    cfg=state_settings(configuration(tmp_path, welding=True),postgres_state)
    scheduler=SchedulerProcess(cfg)
    consumer=WorkerProcess(cfg)
    consumer.owner=consumer.repository.acquire('worker:test-consumer',ttl=600)
    payload=database_context(scheduler.runtime).lines['welding'].report_provider.refresh_plan()[0][0]
    scheduler._schedule_snapshot('welding',payload,300)
    task=consumer.repository.claim_task(consumer.owner,analysis=False)
    clock[0]+=15;clock[1]+=15
    consumer._completed.append((task,None))
    with consumer.scope(): consumer._flush_completions()
    calls=Mock(wraps=scheduler.repository.snapshot_refresh_due)
    monkeypatch.setattr(scheduler.repository,'snapshot_refresh_due',calls)
    clock[0]+=285;clock[1]+=285
    scheduler._schedule_snapshot('welding',payload,300)
    assert calls.call_count==1
    assert scheduler.repository.task_counts()=={'completed':1}
    clock[0]+=15;clock[1]+=15
    scheduler._schedule_snapshot('welding',payload,300)
    assert scheduler.repository.task_counts()=={'pending':1}
    assert not hasattr(consumer,'_next_due')


def test_retired_snapshot_task_finishes_without_recreating_its_removed_plan(postgres_state, tmp_path):
    from tests.line_fixtures import configuration
    process = WorkerProcess(state_settings(configuration(tmp_path),postgres_state))
    process.owner = process.repository.acquire('worker:test-consumer', ttl=30)
    process.repository.enqueue('report_snapshot_refresh', 'welding', {})
    task = process.repository.claim_task(process.owner, analysis=False)
    with process.scope():
        process._execute(task)
        assert process._flush_completions() == 1
    assert process.repository.task_counts() == {'completed': 1}
    assert not hasattr(process, "_next_due")
