from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.database.workshop import (
    AuthenticationError,
    ConfirmationConflictError,
    DailyReportExistsError,
    WorkshopRepository,
    hash_password,
    verify_password,
)
from app.models.domain import WorkshopRecord


def test_password_hash_is_salted_and_verifiable() -> None:
    first = hash_password("safe-password")
    second = hash_password("safe-password")

    assert first != second
    assert verify_password("safe-password", first)
    assert not verify_password("wrong-password", first)


def test_account_session_and_single_worker_confirmation(tmp_path: Path) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db")
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
    assert saved_records[0].confirmed_by == "张三"
    assert admin["role"] == "admin"


def test_empty_daily_report_can_be_populated_when_source_data_arrives(
    tmp_path: Path,
) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db")
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
