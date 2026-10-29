from collections import Counter
from dataclasses import replace
import sqlite3

import pytest

from app.database.sqlite import ExecutionLeaseLost
from app.services.application_runtime import build_runtime
from app.worker import WorkerProcess
from test_web_worker_processes import configuration


def test_periodic_rounds_are_durable_device_scoped_and_do_not_duplicate_reconciliation(tmp_path, monkeypatch):
    from app.database import coordination
    now = [coordination.time()]
    monkeypatch.setattr(coordination, "time", lambda: now[0])
    configured = replace(configuration(tmp_path), data_source_mode="sqlserver", llm_enabled=True,
                         leadership_report_reconcile_hour=0)
    process = WorkerProcess(configured)
    process._schedule()
    with process.repository.connect() as connection:
        rows = [dict(row) for row in connection.execute("SELECT kind,device_id,payload FROM background_tasks")]
    for device_id in process.runtime.devices:
        assert Counter(row["kind"] for row in rows if row["device_id"] == device_id) == {
            "current_refresh": 1, "leadership_refresh": 3,
        }
        current = process.runtime.devices[device_id].current_report_refresh_worker
        from datetime import date
        current._schedule_analysis("workshop", (date(2026, 10, 24),))
        current._schedule_analysis("workshop", (date(2026, 10, 24),))
    assert process.repository.task_counts()["pending"] == len(rows) + 4
    replacement = WorkerProcess(configured)
    replacement._schedule()
    assert replacement.repository.task_counts() == process.repository.task_counts()
    assert replacement.repository.task_counts()["pending"] == len(rows) + 4
    now[0] += 301
    replacement._schedule()
    with process.repository.connect() as connection:
        rounds = connection.execute("SELECT device_id, COUNT(*) FROM background_tasks WHERE kind='current_refresh' GROUP BY device_id").fetchall()
    assert dict(rounds) == {device_id: 2 for device_id in process.runtime.devices}
    assert not process.operations.threads and not process.analysis.threads


def test_task_failure_retries_after_backoff_and_stale_owner_cannot_finish(tmp_path, monkeypatch):
    process = WorkerProcess(configuration(tmp_path))
    repository = process.repository
    process.owner = repository.acquire("worker", ttl=30)
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
    process.owner = repository.acquire("worker", ttl=30)
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
def test_malformed_snapshot_task_is_recorded_as_failed_without_breaking_completion(tmp_path, payload, caplog):
    process = WorkerProcess(configuration(tmp_path))
    queue = process.repository
    process.owner = queue.acquire('worker', ttl=30)
    queue.enqueue('report_snapshot_refresh', 'welding', payload)
    try:
        task = queue.claim_task(process.owner, analysis=False)
        with caplog.at_level('INFO'), process.scope():
            process._execute(task)
            process._flush_completions()
        assert queue.task_counts() == {'deferred': 1}
        assert 'outcome=failed elapsed_ms=' in caplog.text
        with queue.connect() as connection:
            assert connection.execute('SELECT last_error FROM background_tasks').fetchone()[0]
    finally:
        process.stop()


def test_process_migration_backs_up_and_preserves_legacy_business_rows(tmp_path):
    configured = configuration(tmp_path)
    legacy = build_runtime(configured, role="worker")
    legacy.metadata.create_job(report_id="legacy", report_date="2026-10-24", project_code=None, ship_no=None,
                               request_id="legacy", created_at="2026-10-24T10:00:00+08:00", device_id="Cutter01")
    legacy.metadata.update_status("legacy", "analyzing", "legacy progress")
    legacy.workshop.bootstrap_admin(username="old-admin", display_name="原管理员", password="test-password",
                                    now="2026-10-24T10:00:00+08:00")
    with legacy.sqlite_database.connect() as connection:
        for table in ("report_jobs", "email_deliveries", "energy_history_queue"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN execution_owner")
        for table in ("process_leases", "background_tasks", "background_schedules", "llm_waiters", "analysis_generations"):
            connection.execute(f"DROP TABLE {table}")
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        columns = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] for table in tables}
        before = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}"')] for table in tables}
    upgraded = build_runtime(configured, role="worker")
    backups = list((configured.data_dir / "backups").glob("metadata-before-processes-*.db"))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o777 == 0o600
    with upgraded.sqlite_database.connect() as connection, sqlite3.connect(backups[0]) as backup:
        for table in tables:
            selection = ",".join(f'"{column}"' for column in columns[table])
            assert [tuple(row) for row in connection.execute(f'SELECT {selection} FROM "{table}"')] == before[table], table
            assert [tuple(row) for row in backup.execute(f'SELECT * FROM "{table}"')] == before[table], table
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    build_runtime(configured, role="worker")
    assert len(list((configured.data_dir / "backups").glob("metadata-before-processes-*.db"))) == 1
    assert upgraded.metadata.get_job("legacy")["status"] == "analyzing"


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
