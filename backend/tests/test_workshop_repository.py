from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.database.workshop import (
    AuthenticationError,
    ConfirmationConflictError,
    ConfirmationNotFoundError,
    ConfirmationPermissionError,
    DailyReportExistsError,
    WorkshopRepository,
    hash_password,
    verify_password,
)
from app.models.domain import WorkshopRecord


def test_hr_admin_permission_mapping_keeps_hr_password_external(tmp_path: Path) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = "2026-09-09T10:30:00+08:00"
    operator = repository.bootstrap_admin(
        username="emergency-admin",
        display_name="应急管理员",
        password="current-admin-password",
        now=now,
    )
    identity = {
        "external_id": "9123",
        "company_no": "C0007",
        "group_no": "G0021",
        "display_name": "张三",
    }

    granted = repository.grant_external_admin(
        identity=identity,
        operator=operator,
        now=now,
    )

    assert granted["role"] == "admin"
    assert granted["auth_source"] == "hr_database"
    assert repository.verify_local_user_password(
        operator["user_id"], "current-admin-password"
    )
    assert not repository.verify_local_user_password(granted["user_id"], "anything")
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT password_hash FROM users WHERE user_id = ?", (granted["user_id"],)
        ).fetchone()
    assert row["password_hash"] == ""

    refreshed = repository.upsert_external_worker(**identity, now=now)
    assert refreshed["role"] == "admin"
    admins = repository.list_admins()
    mapped = next(item for item in admins if item["user_id"] == granted["user_id"])
    assert mapped["permission_changed_by"] == "应急管理员"

    with pytest.raises(ValueError, match="自己"):
        repository.revoke_external_admin(
            granted["user_id"], operator=granted, now=now
        )

    revoked = repository.revoke_external_admin(
        granted["user_id"], operator=operator, now=now
    )
    assert revoked["role"] == "worker"


def test_scoped_snapshot_keeps_existing_rows_when_new_tasks_arrive(tmp_path: Path) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db", scope_machine_no='Cutter01')
    repository.initialize()
    now = "2026-09-09T10:30:00+08:00"
    admin = repository.bootstrap_admin(
        username="admin",
        display_name="管理员",
        password="admin-password",
        now=now,
    )
    legacy = WorkshopRecord(
        task_id="legacy-cutter02",
        project_code="P2",
        ship_no="P2",
        segment_name=None,
        program_no="OLD",
        cut_length=Decimal("2"),
        groove_length=Decimal("0"),
        work_time=Decimal("20"),
    )
    scoped = WorkshopRecord(
        task_id="cutter01-task",
        project_code="P1",
        ship_no="P1",
        segment_name=None,
        program_no="NEW",
        cut_length=Decimal("1"),
        groove_length=Decimal("0"),
        work_time=Decimal("10"),
    )
    repository.create_daily_report(
        report_date="2026-09-09",
        source_mode="sqlserver",
        records=[legacy],
        user=admin,
        now=now,
        scope_machine_no=None,
    )

    rebuilt = repository.populate_empty_daily_report(
        report_date="2026-09-09",
        source_mode="sqlserver",
        records=[scoped],
        user=admin,
        now=now,
        scope_machine_no="Cutter01",
    )
    _, records = repository.get_daily_report_records("2026-09-09")

    assert rebuilt["scope_machine_no"] == "Cutter01"
    assert [record.task_id for record in records] == ["legacy-cutter02", "cutter01-task"]


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("safe-password")
    second = hash_password("safe-password")

    assert first != second
    assert verify_password("safe-password", first)
    assert not verify_password("wrong-password", first)


def test_account_session_and_single_worker_confirmation(tmp_path: Path) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db", scope_machine_no='Cutter01')
    repository.initialize()
    now = "2026-08-19T08:00:00+08:00"
    admin = repository.bootstrap_admin(
        username="admin",
        display_name="管理员",
        password="admin-password",
        now=now,
    )
    worker = repository.create_user(
        username="worker01",
        display_name="张三",
        password="worker-password",
        role="worker",
        now=now,
    )
    other = repository.create_user(
        username="worker02",
        display_name="李四",
        password="worker-password",
        role="worker",
        now=now,
    )

    assert repository.authenticate("WORKER01", "worker-password")["user_id"] == worker["user_id"]
    with pytest.raises(AuthenticationError):
        repository.authenticate("worker01", "wrong-password")
    with pytest.raises(AuthenticationError):
        repository.bootstrap_admin(
            username="second-admin",
            display_name="其他管理员",
            password="admin-password",
            now=now,
        )

    token, _ = repository.create_session(
        worker["user_id"], now=datetime.fromisoformat(now), hours=12
    )
    assert repository.user_for_session(token, now=now)["display_name"] == "张三"
    repository.delete_session(token)
    assert repository.user_for_session(token, now=now) is None

    report = repository.create_daily_report(
        report_date="2026-08-19",
        source_mode="demo",
        records=[
            WorkshopRecord(
                task_id="24820",
                project_code="PJT-01",
                ship_no="PJT-01",
                segment_name="S01",
                program_no="OP-01",
                cut_length=Decimal("10.5"),
                groove_length=Decimal("2.5"),
                work_time=Decimal("90"),
                production_time=datetime.fromisoformat(now),
            )
        ],
        user=admin,
        now=now,
    )
    assert report["item_count"] == 1
    assert repository.get_daily_report("2026-08-19")["report_id"] == report["report_id"]
    with pytest.raises(DailyReportExistsError):
        repository.create_daily_report(
            report_date="2026-08-19",
            source_mode="demo",
            records=[],
            user=admin,
            now=now,
        )

    confirmation = repository.confirm_task(
        task_id="24820", report_id=report["report_id"], user=worker, now=now
    )
    assert confirmation["worker_name"] == "张三"
    assert repository.confirm_task(
        task_id="24820", report_id=report["report_id"], user=worker, now=now
    )["worker_id"] == worker["user_id"]
    with pytest.raises(ConfirmationConflictError, match="张三"):
        repository.confirm_task(
            task_id="24820", report_id=report["report_id"], user=other, now=now
        )
    assert repository.get_confirmations(["24820"])["24820"]["worker_name"] == "张三"
    saved_report, saved_records = repository.get_daily_report_records("2026-08-19")
    assert saved_report["confirmed_count"] == 1
    assert saved_records[0].project_code == saved_records[0].ship_no == "PJT-01"
    assert saved_records[0].confirmed_by_id == worker["user_id"]
    assert saved_records[0].confirmed_by == "张三"
    assert saved_records[0].confirmed_by_company_no is None
    assert admin["role"] == "admin"

    with pytest.raises(ConfirmationPermissionError, match="本人"):
        repository.revoke_confirmation(
            task_id="24820",
            report_id=report["report_id"],
            user=other,
            reason="误点了别人的任务",
            now="2026-08-19T08:05:00+08:00",
        )

    revoked = repository.revoke_confirmation(
        task_id="24820",
        report_id=report["report_id"],
        user=worker,
        reason="误点了别人的任务",
        now="2026-08-19T08:06:00+08:00",
    )
    assert revoked["action"] == "worker_revoke"
    assert repository.get_confirmations(["24820"]) == {}
    with pytest.raises(ConfirmationNotFoundError):
        repository.revoke_confirmation(
            task_id="24820",
            report_id=report["report_id"],
            user=worker,
            reason="重复撤销",
            now="2026-08-19T08:07:00+08:00",
        )

    repository.confirm_task(
        task_id="24820",
        report_id=report["report_id"],
        user=worker,
        now="2026-08-19T08:08:00+08:00",
    )
    reassigned = repository.correct_confirmation(
        task_id="24820",
        report_id=report["report_id"],
        admin=admin,
        reason="现场记录确认实际完成人为李四",
        new_worker_id=other["user_id"],
        now="2026-08-19T08:09:00+08:00",
    )
    assert reassigned["action"] == "admin_reassign"
    assert repository.get_confirmations(["24820"])["24820"]["worker_name"] == "李四"

    admin_revoked = repository.correct_confirmation(
        task_id="24820",
        report_id=report["report_id"],
        admin=admin,
        reason="等待现场重新确认",
        now="2026-08-19T08:10:00+08:00",
    )
    assert admin_revoked["action"] == "admin_revoke"
    assert repository.get_daily_report("2026-08-19")["confirmed_count"] == 0
    audits = repository.list_confirmation_audits(
        task_id="24820", report_id=report["report_id"]
    )
    assert [item["action"] for item in audits] == [
        "admin_revoke", "admin_reassign", "confirm", "worker_revoke", "confirm"
    ]
    assert audits[0]["reason"] == "等待现场重新确认"


def test_external_worker_is_upserted_without_storing_hr_password(tmp_path: Path) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = "2026-08-24T09:00:00+08:00"

    worker = repository.upsert_external_worker(
        external_id="9123",
        company_no="C0007",
        group_no="G0021",
        display_name="张三",
        now=now,
    )

    assert worker["username"] == "C0007"
    assert worker["external_id"] == "9123"
    assert worker["auth_source"] == "hr_database"
    assert worker["role"] == "worker"
    with repository.connect() as connection:
        stored = connection.execute(
            "SELECT username, password_hash FROM users WHERE user_id = ?",
            (worker["user_id"],),
        ).fetchone()
    assert stored["username"] == "hr:9123"
    assert stored["password_hash"] == ""
    with pytest.raises(AuthenticationError):
        repository.authenticate("C0007", "any-password")

    updated = repository.upsert_external_worker(
        external_id="9123",
        company_no="C0008",
        group_no="G0021",
        display_name="张三（新姓名）",
        now="2026-08-24T10:00:00+08:00",
    )
    assert updated["user_id"] == worker["user_id"]
    assert updated["username"] == "C0008"
    assert updated["display_name"] == "张三（新姓名）"
    repository.confirm_task(task_id="hr-task", user=updated, now=now)
    assert repository.get_confirmations(["hr-task"])["hr-task"]["worker_company_no"] == "C0008"


def test_external_worker_does_not_prevent_admin_bootstrap(tmp_path: Path) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = "2026-08-24T09:00:00+08:00"
    repository.upsert_external_worker(
        external_id="9123",
        company_no="C0007",
        group_no="G0021",
        display_name="张三",
        now=now,
    )

    assert repository.admin_count() == 0
    admin = repository.bootstrap_admin(
        username="admin",
        display_name="管理员",
        password="admin-password",
        now=now,
    )
    assert admin["role"] == "admin"
    assert repository.admin_count() == 1


def test_empty_daily_report_can_be_populated_when_source_data_arrives(
    tmp_path: Path,
) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db", scope_machine_no='Cutter01')
    repository.initialize()
    now = "2026-08-19T08:00:00+08:00"
    admin = repository.bootstrap_admin(
        username="admin",
        display_name="管理员",
        password="admin-password",
        now=now,
    )
    empty = repository.create_daily_report(
        report_date="2026-08-19",
        source_mode="excel",
        records=[],
        user=admin,
        now=now,
    )
    assert empty["item_count"] == 0

    populated = repository.populate_empty_daily_report(
        report_date="2026-08-19",
        source_mode="excel",
        records=[
            WorkshopRecord(
                task_id="new-task",
                project_code=None,
                ship_no=None,
                segment_name=None,
                program_no="NEW.GEN",
                cut_length=Decimal("1.5"),
                groove_length=Decimal("0"),
                work_time=Decimal("60"),
            )
        ],
        user=admin,
        now="2026-08-19T09:00:00+08:00",
    )
    assert populated["report_id"] == empty["report_id"]
    assert populated["item_count"] == 1
    _, records = repository.get_daily_report_records("2026-08-19")
    assert records[0].task_id == "new-task"


def test_batch_confirmation_is_atomic_when_one_task_has_another_worker(
    tmp_path: Path,
) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db", scope_machine_no='Cutter01')
    repository.initialize()
    now = "2026-09-11T09:00:00+08:00"
    admin = repository.bootstrap_admin(
        username="admin",
        display_name="管理员",
        password="admin-password",
        now=now,
    )
    worker = repository.upsert_external_worker(
        external_id="worker-1",
        company_no="C0001",
        group_no="G0001",
        display_name="张三",
        now=now,
    )
    other = repository.upsert_external_worker(
        external_id="worker-2",
        company_no="C0002",
        group_no="G0002",
        display_name="李四",
        now=now,
    )
    records = [
        WorkshopRecord(
            task_id=f"task-{index}",
            project_code="P001",
            ship_no="S001",
            segment_name="A01",
            program_no=f"PROGRAM-{index}.GEN",
            cut_length=Decimal("10"),
            groove_length=Decimal("0"),
            work_time=Decimal("60"),
        )
        for index in range(1, 4)
    ]
    report = repository.create_daily_report(
        report_date="2026-09-11",
        source_mode="demo",
        records=records,
        user=admin,
        now=now,
    )
    repository.confirm_task(
        task_id="task-2",
        report_id=report["report_id"],
        user=other,
        now=now,
    )

    with pytest.raises(ConfirmationConflictError, match="李四"):
        repository.confirm_tasks(
            task_ids=["task-1", "task-2"],
            report_id=report["report_id"],
            user=worker,
            now="2026-09-11T09:05:00+08:00",
        )

    assert "task-1" not in repository.get_confirmations(["task-1"])
    confirmations = repository.confirm_tasks(
        task_ids=["task-1", "task-3", "task-1"],
        report_id=report["report_id"],
        user=worker,
        now="2026-09-11T09:06:00+08:00",
    )
    assert [item["task_id"] for item in confirmations] == ["task-1", "task-3"]
    assert all(item["worker_name"] == "张三" for item in confirmations)
