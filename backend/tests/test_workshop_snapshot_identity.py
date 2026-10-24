import sqlite3

import pytest

from app.database.workshop import ConfirmationConflictError, DailyReportRecordMismatchError, DailyReportScopeMismatchError
from test_workshop_snapshot_sync import record, setup


def sync_arguments(repository):
    return dict(report_date="2026-09-30", source_mode="sqlserver",
                user={"user_id": repository.test_admin_id, "display_name": "管理员", "role": "admin"},
                now="2026-10-01T01:00:00", scope_machine_no="Cutter01")


def prepare_phase(repository, arguments, phase):
    if phase != "new":
        repository.sync_daily_report(**arguments, records=[] if phase == "empty" else [record("S")])
    return arguments


@pytest.mark.parametrize("phase", ["new", "empty", "existing"])
def test_ambiguous_task_ids_reject_entire_sync_without_selecting_first_row(tmp_path, phase):
    repository = setup(tmp_path)
    arguments = prepare_phase(repository, sync_arguments(repository), phase)
    before_report, before_records = repository.get_daily_report_records(arguments["report_date"])
    with pytest.raises(DailyReportRecordMismatchError):
        repository.sync_daily_report(**arguments, records=[record("A"), record("A", work=90)])
    after_report, after_records = repository.get_daily_report_records(arguments["report_date"])
    assert after_report == before_report
    assert after_records == before_records


@pytest.mark.parametrize("phase", ["new", "empty", "existing"])
def test_numerically_equivalent_duplicates_remain_idempotent(tmp_path, phase):
    repository = setup(tmp_path)
    arguments = prepare_phase(repository, sync_arguments(repository), phase)
    first = repository.sync_daily_report(**arguments, records=[record("A"), record("A", work="60.0")])
    repeated = repository.sync_daily_report(**arguments, records=[record("A", work="60.00")])
    assert repeated == first
    _, rows = repository.get_daily_report_records(arguments["report_date"])
    assert [row.task_id for row in rows] == (["S", "A"] if phase == "existing" else ["A"])


@pytest.mark.parametrize("empty_rebuild", [False, True])
@pytest.mark.parametrize("legacy_parent", [False, True])
def test_scope_change_is_rejected_without_touching_snapshot_revision(tmp_path, empty_rebuild, legacy_parent):
    repository = setup(tmp_path)
    arguments = sync_arguments(repository)
    report = repository.sync_daily_report(**arguments, records=[record("A")])
    with repository.connect() as connection:
        connection.execute("UPDATE workshop_report_items SET snapshot_revision = 2 WHERE task_id = 'A'")
    if legacy_parent:
        with repository.connect() as connection:
            connection.execute("ALTER TABLE workshop_reports DROP COLUMN snapshot_revision_floor")
        repository.initialize()
    before = repository.get_daily_report_records(arguments["report_date"])
    with pytest.raises(DailyReportScopeMismatchError):
        repository.sync_daily_report(**{**arguments, "scope_machine_no": "Cutter02"},
                                     records=[] if empty_rebuild else [record("A", work=120)])
    assert repository.get_daily_report_records(arguments["report_date"]) == before
    worker = {"user_id": repository.test_worker_id, "display_name": "工人", "role": "worker"}
    for old_revision in (None, 1):
        with pytest.raises(ConfirmationConflictError):
            repository.confirm_task(task_id="A", report_id=report["report_id"], user=worker,
                                    now=arguments["now"], snapshot_revision=old_revision)
    repository.confirm_task(task_id="A", report_id=report["report_id"], user=worker,
                            now=arguments["now"], snapshot_revision=2)


def test_failed_append_restores_items_and_revision_barrier(tmp_path):
    repository = setup(tmp_path)
    arguments = sync_arguments(repository)
    repository.sync_daily_report(**arguments, records=[record("A")])
    before = repository.get_daily_report_records(arguments["report_date"])
    with repository.connect() as connection:
        connection.execute("""CREATE TRIGGER fail_append BEFORE INSERT ON workshop_report_items
            WHEN NEW.task_id = 'B' BEGIN SELECT RAISE(ABORT, 'insert unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="insert unavailable"):
        repository.sync_daily_report(**arguments, records=[record("A"), record("B")])
    assert repository.get_daily_report_records(arguments["report_date"]) == before
    with repository.connect() as connection:
        assert connection.execute("SELECT snapshot_revision_floor FROM workshop_reports").fetchone()[0] == 1
