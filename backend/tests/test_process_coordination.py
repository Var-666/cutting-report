import multiprocessing as mp
from pathlib import Path

import pytest
from threading import Event

from app.database.coordination import CoordinationRepository
from app.database.sqlite import SQLiteDatabase, ExecutionLeaseLost, execution_scope


def _claim(path, output):
    repository = CoordinationRepository(SQLiteDatabase(Path(path)))
    output.put(repository.acquire("worker", ttl=10))


def _late_write(path, owner, entered, resume, output):
    database = SQLiteDatabase(Path(path))
    try:
        with execution_scope(database.path, "worker", owner), database.connect() as connection:
            entered.set()
            assert resume.wait(10)
            connection.execute("INSERT INTO example VALUES ('stale')")
    except ExecutionLeaseLost:
        output.put("fenced")


def _claim_report(path, owner, output):
    from app.database.metadata import MetadataRepository
    database = SQLiteDatabase(Path(path))
    with execution_scope(database.path, "worker", owner):
        output.put(MetadataRepository(database).claim_next_job("2026-10-24T10:00:00+08:00") is not None)


def test_only_one_process_can_acquire_worker_lease(tmp_path):
    database = SQLiteDatabase(tmp_path / "metadata.db")
    repository = CoordinationRepository(database)
    repository.initialize()
    context = mp.get_context("spawn")
    output = context.Queue()
    processes = [context.Process(target=_claim, args=(str(database.path), output)) for _ in range(3)]
    for process in processes:
        process.start()
    results = [output.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sum(owner is not None for owner in results) == 1


def test_expired_owner_cannot_commit_over_a_new_owner(tmp_path):
    database = SQLiteDatabase(tmp_path / "metadata.db")
    repository = CoordinationRepository(database)
    repository.initialize()
    old = repository.acquire("worker", ttl=10)
    with database.connect() as connection:
        connection.execute("CREATE TABLE example (value TEXT)")
        connection.execute("UPDATE process_leases SET expires_at=0 WHERE name='worker'")
    new = repository.acquire("worker", ttl=10)
    assert new and new != old
    assert not repository.renew("worker", old, ttl=10)
    with pytest.raises(ExecutionLeaseLost), execution_scope(database.path, "worker", old):
        with database.connect() as connection:
            connection.execute("INSERT INTO example VALUES ('stale')")
    with execution_scope(database.path, "worker", new), database.connect() as connection:
        connection.execute("INSERT INTO example VALUES ('current')")
    with database.connect() as connection:
        assert [row[0] for row in connection.execute("SELECT value FROM example")] == ["current"]


def test_lease_renewal_rechecks_time_after_waiting_for_the_write_lock(tmp_path, monkeypatch):
    from app.database import coordination
    now = [100.0]
    monkeypatch.setattr(coordination, "time", lambda: now[0])
    repository = CoordinationRepository(SQLiteDatabase(tmp_path / "metadata.db"))
    repository.initialize()
    owner = repository.acquire("worker", ttl=10)
    original = repository.begin_immediate

    def wait_for_write_lock(connection):
        original(connection)
        now[0] += 20

    monkeypatch.setattr(repository, "begin_immediate", wait_for_write_lock)
    assert not repository.renew("worker", owner, ttl=10)
    new = repository.acquire("worker", ttl=10)
    assert new and new != owner
    with repository.connect() as connection:
        row = connection.execute("SELECT heartbeat_at, expires_at FROM process_leases").fetchone()
        assert row["heartbeat_at"] == 140
        assert row["expires_at"] == 150


def test_queue_is_bounded_and_overflow_is_durable_and_recoverable(tmp_path):
    repository = CoordinationRepository(SQLiteDatabase(tmp_path / "metadata.db"))
    repository.initialize()
    for index in range(3):
        repository.enqueue("analysis_workshop", "Cutter01", {"round": index}, key=str(index), capacity=2)
    assert repository.task_counts() == {"pending": 2, "deferred": 1}
    owner = repository.acquire("worker", ttl=10)
    first = repository.claim_task(owner, analysis=True)
    assert first
    repository.finish_task(first, owner)
    repository.admit_deferred(capacity=2)
    assert repository.task_counts() == {"completed": 1, "pending": 2}


def test_expiry_fences_a_transaction_opened_by_another_process_before_takeover(tmp_path):
    context = mp.get_context("spawn")
    database = SQLiteDatabase(tmp_path / "metadata.db")
    repository = CoordinationRepository(database)
    repository.initialize()
    owner = repository.acquire("worker", ttl=10)
    with database.connect() as connection:
        connection.execute("CREATE TABLE example (value TEXT)")
    entered, resume, output = context.Event(), context.Event(), context.Queue()
    writer = context.Process(target=_late_write, args=(str(database.path), owner, entered, resume, output))
    writer.start()
    try:
        assert entered.wait(10)
        with database.connect() as connection:
            connection.execute("UPDATE process_leases SET expires_at=0")
        new = repository.acquire("worker", ttl=10)
        with execution_scope(database.path, "worker", new), database.connect() as connection:
            connection.execute("INSERT INTO example VALUES ('current')")
        resume.set()
        assert output.get(timeout=10) == "fenced"
        writer.join(10)
        assert writer.exitcode == 0
        with database.connect() as connection:
            assert [row[0] for row in connection.execute("SELECT value FROM example")] == ["current"]
    finally:
        if writer.is_alive():
            writer.kill()
            writer.join(10)


def test_report_claim_is_atomic_between_processes(tmp_path):
    from app.database.metadata import MetadataRepository
    context = mp.get_context("spawn")
    database = SQLiteDatabase(tmp_path / "metadata.db")
    repository = CoordinationRepository(database)
    repository.initialize()
    metadata = MetadataRepository(database, device_id='Cutter01')
    metadata.initialize()
    metadata.create_job(report_id="one", report_date="2026-10-24", project_code=None, ship_no=None,
                        request_id="one", created_at="2026-10-24T10:00:00+08:00")
    owner, output = repository.acquire("worker", ttl=10), context.Queue()
    claimers = [context.Process(target=_claim_report, args=(str(database.path), owner, output)) for _ in range(3)]
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


def test_energy_snapshot_compare_and_swap_keeps_concurrent_result(tmp_path):
    from app.database.energy import EnergyReportRepository
    repository = EnergyReportRepository(SQLiteDatabase(tmp_path / "metadata.db"))
    repository.initialize()
    original = {"report_date": "2026-10-24", "updated_at": "same-second", "rows": []}
    newer = {**original, "rows": [{"value": 1}]}
    assert repository.save_day("device-source", original, previous=None)
    assert repository.save_day("device-source", newer, previous=original)
    assert not repository.save_day("device-source", original, previous=original)
    assert repository.get_day("device-source", "2026-10-24") == newer
