from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from time import time

import pytest

from app.database.coordination import CoordinationRepository
from app.services.database_runtime import database_context
from app.worker import WorkerProcess


@pytest.fixture
def periodic_worker(postgres_state, monkeypatch):
    clock = [time()]
    monkeypatch.setattr('app.database.coordination.time', lambda: clock[0])
    monkeypatch.setattr('app.worker.time', lambda: clock[0])
    process = WorkerProcess(replace(postgres_state[1], report_background_refresh_seconds=300))
    process.owner = process.repository.acquire(process.lease_name, ttl=60)
    line = database_context(process.runtime).lines['Cutter01']
    calls = []
    monkeypatch.setattr(line.background, 'handle_background', lambda kind, payload: calls.append((kind, payload)))
    payload = {'date': '2026-11-12', 'source_fingerprint': line.settings.source_fingerprint}
    try:
        yield process, clock, payload, calls
    finally:
        process.stop()


@pytest.mark.parametrize('kind', ['analysis_leadership', 'analysis_workshop', 'analysis_equipment'])
def test_pending_rounds_merge_only_with_the_same_device_source_and_period(postgres_state, kind):
    queue = CoordinationRepository(postgres_state[0])
    payload = {'date': '2026-11-12', 'source_fingerprint': 'source-one'}
    assert queue.enqueue(kind, 'Cutter01', payload)
    assert queue.enqueue(kind, 'Cutter01', dict(reversed(list(payload.items()))))
    assert queue.task_counts() == {'completed': 1, 'pending': 1}
    assert queue.enqueue(kind, 'Cutter02', payload)
    assert queue.enqueue(kind, 'Cutter01', {**payload, 'source_fingerprint': 'source-two'})
    assert queue.enqueue(kind, 'Cutter01', {**payload, 'date': '2026-11-11'})
    assert queue.task_counts() == {'completed': 1, 'pending': 4}
    with queue.connect() as connection:
        assert connection.execute("SELECT last_error FROM background_tasks WHERE status='completed'").fetchone()['last_error'] == 'SUPERSEDED'


def test_concurrent_enqueues_keep_one_pending_round_and_do_not_touch_running_claim(postgres_state):
    queue = CoordinationRepository(postgres_state[0])
    payload = {'date': '2026-11-12', 'source_fingerprint': 'source-one'}
    owner = queue.acquire('worker:periodic-test', ttl=60)
    queue.enqueue('analysis_workshop', 'Cutter01', payload)
    running = queue.claim_task(owner, analysis=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(lambda _: queue.enqueue('analysis_workshop', 'Cutter01', payload), range(6)))
    assert queue.task_counts() == {'completed': 5, 'running': 1, 'pending': 1}
    assert queue.finish_task(running, owner)
    following = queue.claim_task(owner, analysis=True)
    assert following['task_id'] != running['task_id']
    assert queue.finish_task(following, owner)


def test_failed_periodic_analysis_retries_once_then_yields_to_the_next_round(periodic_worker, monkeypatch):
    process, clock, payload, _ = periodic_worker
    queue = process.repository
    monkeypatch.setattr(process, '_handle', lambda task: (_ for _ in ()).throw(TimeoutError('model unavailable')))
    queue.enqueue('analysis_workshop', 'Cutter01', payload)
    first = queue.claim_task(process.owner, analysis=True)
    with process.scope():
        process._execute(first)
        process._flush_completions()
    assert queue.task_counts() == {'deferred': 1}
    queue.admit_deferred()
    assert queue.claim_task(process.owner, analysis=True) is None
    clock[0] += 30
    queue.admit_deferred()
    second = queue.claim_task(process.owner, analysis=True)
    assert second['task_id'] == first['task_id'] and second['attempts'] == 2
    with process.scope():
        process._execute(second)
        process._flush_completions()
    assert queue.task_counts() == {'blocked': 1}
    clock[0] += 300
    queue.admit_deferred()
    assert queue.claim_task(process.owner, analysis=True) is None
    queue.enqueue('analysis_workshop', 'Cutter01', payload)
    fresh = queue.claim_task(process.owner, analysis=True)
    assert fresh['task_id'] != first['task_id'] and fresh['attempts'] == 1
    assert queue.finish_task(fresh, process.owner)


def test_new_round_supersedes_backing_off_analysis_without_waiting_for_its_retry(periodic_worker):
    process, clock, payload, _ = periodic_worker
    queue = process.repository
    queue.enqueue('analysis_equipment', 'Cutter01', payload)
    failed = queue.claim_task(process.owner, analysis=True)
    queue.finish_task(failed, process.owner, 'AnalysisUnavailableError')
    clock[0] += 10
    queue.enqueue('analysis_equipment', 'Cutter01', payload)
    assert queue.task_counts() == {'completed': 1, 'pending': 1}
    with queue.connect() as connection:
        assert connection.execute('SELECT last_error FROM background_tasks WHERE task_id=%s',
                                  (failed['task_id'],)).fetchone()['last_error'] == 'SUPERSEDED:AnalysisUnavailableError'
    fresh = queue.claim_task(process.owner, analysis=True)
    assert fresh['task_id'] != failed['task_id']
    queue.finish_task(fresh, process.owner)


@pytest.mark.parametrize('kind', ['analysis_leadership', 'analysis_workshop', 'analysis_equipment', 'current_refresh', 'leadership_refresh'])
def test_expired_periodic_work_does_not_call_its_business_handler(periodic_worker, kind):
    process, clock, payload, calls = periodic_worker
    queue = process.repository
    key = f'{kind}:Cutter01:old-round' if kind.endswith('_refresh') else None
    queue.enqueue(kind, 'Cutter01', payload, key=key)
    clock[0] += 300
    task = queue.claim_task(process.owner, analysis=kind.startswith('analysis_'))
    with process.scope():
        process._execute(task)
        process._flush_completions()
    assert not calls
    assert queue.task_counts() == {'completed': 1}
    queue.enqueue(kind, 'Cutter01', payload, key=f'{kind}:Cutter01:fresh-round' if key else None)
    fresh = queue.claim_task(process.owner, analysis=kind.startswith('analysis_'))
    with process.scope():
        process._execute(fresh)
        process._flush_completions()
    assert calls == [(kind, payload)]


@pytest.mark.parametrize('key', [None, 'reconcile:Cutter01:2026-11-12:2026-10-01'])
def test_manual_and_historical_month_refreshes_are_not_expired(periodic_worker, key):
    process, clock, payload, calls = periodic_worker
    payload = {**payload, 'date': '2026-10-01', 'bootstrap': True}
    process.repository.enqueue('leadership_refresh', 'Cutter01', payload, key=key)
    clock[0] += 3600
    task = process.repository.claim_task(process.owner, analysis=False)
    with process.scope():
        process._execute(task)
        process._flush_completions()
    assert calls == [('leadership_refresh', payload)]
