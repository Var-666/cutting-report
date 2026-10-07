from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
import sqlite3
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from app.database.workshop import (
    ConfirmationConflictError, DailyReportRecordMismatchError,
    DailyReportScopeMismatchError, DailyReportSourceMismatchError,
)
from test_workshop_snapshot_sync import record, setup


@pytest.fixture
def workflow(tmp_path):
    repository = setup(tmp_path)
    admin = {"user_id": repository.test_admin_id, "display_name": "管理员", "role": "admin"}
    worker = {"user_id": repository.test_worker_id, "display_name": "工人", "role": "worker"}
    report = repository.create_daily_report(
        report_date="2026-09-30", source_mode="sqlserver", records=[record(task) for task in "ABC"],
        user=admin, now="2026-10-01T01:00:00",
    )
    repository.confirm_tasks(task_ids=["A", "B"], report_id=report["report_id"],
                             user=worker, now="2026-10-01T02:00:00")
    arguments = dict(report_date="2026-09-30", source_mode="sqlserver", scope_machine_no="Cutter01",
                     records=[record("A", work=90), record("B"), record("C", work=70)])
    return repository, report, admin, worker, arguments


def apply(repository, admin, arguments, version):
    return repository.apply_source_corrections(
        **arguments, expected_version=version, reason="核对生产记录后更正",
        admin=admin, now="2026-10-01T03:00:00",
    )


def test_correction_is_explicit_revokes_only_changed_tasks_and_requires_fresh_confirmation(workflow):
    repository, report, admin, worker, arguments = workflow
    preview = repository.preview_source_corrections(**arguments)
    assert [item["task_id"] for item in preview["items"]] == ["A", "C"]
    assert preview["items"][0]["fields"] == ["work_time"]
    assert repository.get_daily_report("2026-09-30")["confirmed_count"] == 2
    result = apply(repository, admin, arguments, preview["version"])
    assert result["corrected_count"] == 2
    assert result["revoked_count"] == 1
    _, rows = repository.get_daily_report_records("2026-09-30")
    assert [(row.task_id, row.snapshot_revision, row.confirmed_by) for row in rows] == [
        ("A", 2, None), ("B", 1, "工人"), ("C", 2, None),
    ]
    for revision in (None, 1):
        with pytest.raises(ConfirmationConflictError, match="已更正"):
            repository.confirm_task(task_id="A", report_id=report["report_id"], user=worker,
                                    now="2026-10-01T04:00:00", snapshot_revision=revision)
    repository.confirm_task(task_id="A", report_id=report["report_id"], user=worker,
                            now="2026-10-01T04:00:00", snapshot_revision=2)
    audits = repository.list_confirmation_audits(task_id="A")
    assert [audit["action"] for audit in audits] == ["confirm", "admin_revoke", "confirm"]
    latest = repository.preview_source_corrections(**arguments)
    assert latest["items"] == []
    source_audit = next(row for row in latest["audits"] if row["task_id"] == "A")
    assert source_audit["payload"]["old"]["work_time"] == "60"
    assert source_audit["payload"]["new"]["work_time"] == "90"
    assert source_audit["payload"]["previous_confirmation"]["worker_id"] == worker["user_id"]
    with pytest.raises(DailyReportRecordMismatchError, match="重新预览"):
        apply(repository, admin, arguments, preview["version"])
    assert len(repository.preview_source_corrections(**arguments)["audits"]) == 2


@pytest.mark.parametrize("change", ["source", "confirmation", "snapshot"])
def test_changes_after_preview_reject_whole_batch(workflow, change):
    repository, report, admin, worker, arguments = workflow
    preview = repository.preview_source_corrections(**arguments)
    if change == "source":
        arguments = {**arguments, "records": [record("A", work=100), record("B"), record("C", work=70)]}
    elif change == "confirmation":
        repository.confirm_task(task_id="C", report_id=report["report_id"], user=worker,
                                now="2026-10-01T02:30:00")
    else:
        with repository.connect() as connection:
            connection.execute("UPDATE workshop_report_items SET work_time = '65' WHERE task_id = 'A'")
    with pytest.raises(DailyReportRecordMismatchError, match="重新预览"):
        apply(repository, admin, arguments, preview["version"])
    assert repository.get_daily_report("2026-09-30")["confirmed_count"] >= 2
    assert repository.preview_source_corrections(**arguments)["audits"] == []
    assert all(row.snapshot_revision == 1 for row in repository.get_daily_report_records("2026-09-30")[1])


def test_failure_after_first_item_rolls_back_snapshot_revocation_and_both_audits(workflow):
    repository, _, admin, _, arguments = workflow
    preview = repository.preview_source_corrections(**arguments)
    with repository.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_second_source_audit
            BEFORE INSERT ON workshop_source_correction_audits WHEN NEW.task_id = 'C'
            BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="audit unavailable"):
        apply(repository, admin, arguments, preview["version"])
    report, rows = repository.get_daily_report_records("2026-09-30")
    assert report["confirmed_count"] == 2
    assert all(row.work_time == 60 and row.snapshot_revision == 1 for row in rows)
    assert len(repository.list_confirmation_audits(task_id="A")) == 1
    assert repository.preview_source_corrections(**arguments)["audits"] == []


def test_concurrent_corrections_apply_reviewed_version_only_once(workflow):
    repository, _, admin, _, arguments = workflow
    preview = repository.preview_source_corrections(**arguments)

    def submit():
        try:
            apply(repository, admin, arguments, preview["version"])
            return "applied"
        except DailyReportRecordMismatchError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: submit(), range(2))) == ["applied", "stale"]
    assert len(repository.preview_source_corrections(**arguments)["audits"]) == 2
    assert len(repository.list_confirmation_audits(task_id="A")) == 2


@pytest.mark.parametrize("change, expected", [
    ({"source_mode": "demo"}, DailyReportSourceMismatchError),
    ({"scope_machine_no": "Other"}, DailyReportScopeMismatchError),
    ({"records": [record("A", work=90), record("A", work=100)]}, DailyReportRecordMismatchError),
    ({"records": [replace(record("A"), production_date=date(2026, 10, 1))]}, DailyReportRecordMismatchError),
])
def test_correction_cannot_change_source_identity_or_guess_ambiguous_data(workflow, change, expected):
    repository, _, _, _, arguments = workflow
    with pytest.raises(expected):
        repository.preview_source_corrections(**{**arguments, **change})
    assert repository.get_daily_report("2026-09-30")["confirmed_count"] == 2


def test_absent_rows_are_not_deleted_and_new_rows_are_not_corrected(workflow):
    repository, _, _, _, arguments = workflow
    preview = repository.preview_source_corrections(**{**arguments, "records": [record("D")]})
    assert preview["items"] == []
    report, rows = repository.get_daily_report_records("2026-09-30")
    assert report["item_count"] == 3
    assert [row.task_id for row in rows] == ["A", "B", "C"]


def test_http_correction_is_admin_only_fresh_and_confirmed_revision_is_enforced(workflow, monkeypatch, tmp_path):
    from app.api.auth import require_user
    from app.api.workshop import router
    from app.config import Settings
    from app.models.domain import QueryBundle
    from app.database.sqlserver import SqlServerConfigurationError

    repository, _, admin, worker, arguments = workflow
    monkeypatch.setattr("app.api.workshop.settings", Settings(project_root=tmp_path, data_source_mode="sqlserver"))
    calls, invalidations = [], []

    def query(filters, *, force=False):
        assert force is True
        # The source query must execute without holding SQLite's write lock.
        with sqlite3.connect(repository.database_path, timeout=0) as probe:
            probe.execute("BEGIN IMMEDIATE")
        calls.append(filters.report_date)
        return QueryBundle([], arguments["records"], [])

    app = FastAPI()
    app.include_router(router)
    app.state.workshop = repository
    app.state.query_service = SimpleNamespace(fetch_workshop_data=query,
                                             invalidate_workshop_data=lambda: invalidations.append(True))
    user = worker
    app.dependency_overrides[require_user] = lambda: user

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "source-correction"
        return await call_next(request)

    endpoint = "/api/v1/workshop/reports/source-corrections"
    with TestClient(app) as client:
        assert client.post(endpoint + "/preview", json={"report_date": "2026-09-30"}).status_code == 403
        assert client.post(endpoint, json={"report_date": "2026-09-30", "reason": "禁止", "expected_version": "a" * 64}).status_code == 403
        assert calls == []
        user = admin
        preview = client.post(endpoint + "/preview", json={"report_date": "2026-09-30"})
        assert preview.status_code == 200
        body = {"report_date": "2026-09-30", "reason": "核对生产记录", "expected_version": preview.json()["data"]["version"]}
        assert client.post(endpoint, json={**body, "reason": " "}).status_code == 422
        response = client.post(endpoint, json=body)
        assert response.status_code == 200
        assert response.json()["data"]["revoked_count"] == 1
        assert len(calls) == 2
        assert invalidations == [True]
        assert client.post(endpoint, json=body).status_code == 409
        user = worker
        for token in (None, 1):
            payload = {"report_date": "2026-09-30", "task_ids": ["A", "C"]}
            if token is not None:
                payload["snapshot_revisions"] = {"A": token, "C": token}
            assert client.post("/api/v1/workshop/tasks/confirm", json=payload).status_code == 409
        assert repository.get_confirmations(["A", "C"]) == {}
        assert client.post("/api/v1/workshop/tasks/A/confirm", json={"report_date": "2026-09-30"}).status_code == 409
        fresh = client.post("/api/v1/workshop/tasks/confirm", json={
            "report_date": "2026-09-30", "task_ids": ["A", "C"], "snapshot_revisions": {"A": 2, "C": 2},
        })
        assert fresh.status_code == 200
        assert fresh.json()["data"]["confirmed_count"] == 2
        user = admin
        def unavailable(*args, **kwargs):
            raise SqlServerConfigurationError("生产源暂不可用")
        app.state.query_service.fetch_workshop_data = unavailable
        assert client.post(endpoint + "/preview", json={"report_date": "2026-09-30"}).status_code == 503
        assert client.post(endpoint, json=body).status_code == 503
        assert invalidations == [True]


def test_large_correction_batches_are_reviewed_separately(workflow):
    repository, _, admin, _, _ = workflow
    initial = [replace(record(f"task-{index:03}"), production_date=date(2026, 10, 2)) for index in range(205)]
    repository.create_daily_report(report_date="2026-10-02", source_mode="sqlserver",
                                  records=initial, user=admin, now="2026-10-03T00:00:00")
    arguments = dict(report_date="2026-10-02", source_mode="sqlserver", scope_machine_no="Cutter01",
                     records=[replace(row, work_time=90) for row in initial])
    for count in (100, 100, 5):
        preview = repository.preview_source_corrections(**arguments)
        assert len(preview["items"]) == count
        assert apply(repository, admin, arguments, preview["version"])["corrected_count"] == count
    assert repository.preview_source_corrections(**arguments)["total_changes"] == 0
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workshop_source_correction_audits").fetchone()[0] == 205


def test_legacy_snapshot_migration_and_stale_batch_confirmation_are_atomic(workflow):
    repository, report, admin, worker, arguments = workflow
    with repository.connect() as connection:
        connection.execute("ALTER TABLE workshop_report_items DROP COLUMN snapshot_revision")
    repository.initialize()
    assert all(row.snapshot_revision == 1 for row in repository.get_daily_report_records("2026-09-30")[1])
    preview = repository.preview_source_corrections(**arguments)
    apply(repository, admin, arguments, preview["version"])
    with pytest.raises(ConfirmationConflictError):
        repository.confirm_tasks(task_ids=["A", "C"], report_id=report["report_id"], user=worker,
                                 now="2026-10-01T04:00:00", snapshot_revisions={"A": 2, "C": 1})
    assert repository.get_confirmations(["A", "C"]) == {}
