import multiprocessing as mp
from time import sleep
from concurrent.futures import ThreadPoolExecutor
from threading import Timer

import pytest
from threading import Event

from app.database.coordination import CoordinationRepository
from app.database.postgres import PostgresDatabase, ExecutionLeaseLost, execution_scope


def _claim(cfg, output):
    with PostgresDatabase(cfg) as database:
        output.put(CoordinationRepository(database).acquire("worker:test-consumer", ttl=10))


def _late_write(cfg, owner, entered, resume, output):
    with PostgresDatabase(cfg) as database:
        try:
            with execution_scope(database, "worker:test-consumer", owner), database.connect() as connection:
                entered.set()
                assert resume.wait(10)
                connection.execute("INSERT INTO llm_waiters VALUES ('stale', 0)")
        except ExecutionLeaseLost:
            output.put("fenced")


def _claim_report(cfg, owner, output):
    from app.database.metadata import MetadataRepository
    with PostgresDatabase(cfg) as database, execution_scope(database, "worker:test-consumer", owner):
        output.put(MetadataRepository(database).claim_next_job("2026-10-24T10:00:00+08:00") is not None)


def test_only_one_process_can_acquire_worker_lease(postgres_state):
    database = postgres_state[0]
    repository = CoordinationRepository(database)
    context = mp.get_context("spawn")
    output = context.Queue()
    processes = [context.Process(target=_claim, args=(postgres_state[1], output)) for _ in range(3)]
    for process in processes:
        process.start()
    results = [output.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sum(owner is not None for owner in results) == 1


def test_expired_owner_cannot_commit_over_a_new_owner(postgres_state):
    database = postgres_state[0]
    repository = CoordinationRepository(database)
    old = repository.acquire("worker:test-consumer", ttl=10)
    with database.connect() as connection:
        connection.execute("UPDATE process_leases SET expires_at=0 WHERE name='worker:test-consumer'")
    new = repository.acquire("worker:test-consumer", ttl=10)
    assert new and new != old
    assert not repository.renew("worker:test-consumer", old, ttl=10)
    with pytest.raises(ExecutionLeaseLost), execution_scope(database, "worker:test-consumer", old):
        with database.connect() as connection:
            connection.execute("INSERT INTO llm_waiters VALUES ('stale',0)")
    with execution_scope(database, "worker:test-consumer", new), database.connect() as connection:
        connection.execute("INSERT INTO llm_waiters VALUES ('current',0)")
    with database.connect() as connection:
        assert [row["waiter_id"] for row in connection.execute("SELECT waiter_id FROM llm_waiters")] == ["current"]


def test_lease_renewal_rechecks_database_time_after_waiting_for_row_lock(postgres_state):
    database = postgres_state[0]
    repository = CoordinationRepository(database)
    owner = repository.acquire('worker:test-consumer', ttl=0.2)
    with ThreadPoolExecutor(1) as executor:
        with database.connect() as connection:
            connection.execute("SELECT * FROM process_leases WHERE name='worker:test-consumer' FOR UPDATE")
            renewed = executor.submit(repository.renew, 'worker', owner, ttl=10)
            sleep(0.3)
        assert renewed.result(timeout=3) is False
    new = repository.acquire('worker:test-consumer', ttl=10)
    assert new and new != owner



def test_queue_is_bounded_and_overflow_is_durable_and_recoverable(postgres_state):
    repository = CoordinationRepository(postgres_state[0])
    for index in range(3):
        repository.enqueue("analysis_workshop", "Cutter01", {"round": index}, key=str(index), capacity=2)
    assert repository.task_counts() == {"pending": 2, "deferred": 1}
    owner = repository.acquire("worker:test-consumer", ttl=10)
    first = repository.claim_task(owner, analysis=True)
    assert first
    repository.finish_task(first, owner)
    repository.admit_deferred(capacity=2)
    assert repository.task_counts() == {"completed": 1, "pending": 2}


def test_expiry_fences_a_transaction_opened_by_another_process_before_takeover(postgres_state):
    context = mp.get_context('spawn')
    database, cfg, _ = postgres_state
    repository = CoordinationRepository(database)
    owner = repository.acquire('worker:test-consumer', ttl=2)
    entered, resume, output = context.Event(), context.Event(), context.Queue()
    writer = context.Process(target=_late_write, args=(cfg, owner, entered, resume, output))
    writer.start()
    timer = Timer(2.2, resume.set)
    try:
        assert entered.wait(5)
        timer.start()
        # Takeover waits for the writer's shared row lock. Its final expiry
        # check must roll back before the replacement can acquire that row.
        sleep(2.05)
        new = repository.acquire('worker:test-consumer', ttl=10)
        assert new and new != owner
        assert output.get(timeout=5) == 'fenced'
        with execution_scope(database,'worker:test-consumer',new), database.connect() as connection:
            connection.execute("INSERT INTO llm_waiters VALUES ('current',0)")
        writer.join(5)
        assert writer.exitcode == 0
        with database.connect() as connection:
            assert connection.execute('SELECT waiter_id FROM llm_waiters').fetchall() == [{'waiter_id':'current'}]
    finally:
        timer.cancel()
        resume.set()
        if writer.is_alive():
            writer.kill()
        writer.join(5)



def test_report_claim_is_atomic_between_processes(postgres_state):
    from app.database.metadata import MetadataRepository
    context = mp.get_context("spawn")
    database = postgres_state[0]
    repository = CoordinationRepository(database)
    metadata = MetadataRepository(database, device_id='Cutter01')
    metadata.create_job(report_id="one", report_date="2026-10-24", project_code=None, ship_no=None,
                        request_id="one", created_at="2026-10-24T10:00:00+08:00")
    owner, output = repository.acquire("worker:test-consumer", ttl=10), context.Queue()
    claimers = [context.Process(target=_claim_report, args=(postgres_state[1], owner, output)) for _ in range(3)]
    for process in claimers:
        process.start()
    assert sum(output.get(timeout=10) for _ in claimers) == 1
    for process in claimers:
        process.join(10)
        assert process.exitcode == 0
    with pytest.raises(ExecutionLeaseLost):
        metadata.recover_interrupted_jobs()


def test_executor_bounds_running_and_waiting_work():
    from app.workers.bounded_executor import BoundedExecutor, ExecutionQueueFull
    executor = BoundedExecutor(2, 1, "bounded-test")
    release = Event()
    try:
        futures = [executor.submit(release.wait, 3) for _ in range(3)]
        assert len(executor.threads) == 2
        with pytest.raises(ExecutionQueueFull):
            executor.submit(lambda: None)
        release.set()
        assert all(future.result(timeout=2) for future in futures)
    finally:
        release.set()
        assert executor.stop()


def test_energy_snapshot_compare_and_swap_keeps_concurrent_result(postgres_state):
    from app.database.energy import EnergyReportRepository
    repository = EnergyReportRepository(postgres_state[0])
    original = {"report_date": "2026-10-24", "updated_at": "same-second", "rows": []}
    newer = {**original, "rows": [{"value": 1}]}
    assert repository.save_day("device-source", original, previous=None)
    assert repository.save_day("device-source", newer, previous=original)
    assert not repository.save_day("device-source", original, previous=original)
    assert repository.get_day("device-source", "2026-10-24") == newer
