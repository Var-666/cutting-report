from datetime import datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_admin, require_worker
from app.api.workshop import router
from app.database.workshop import WorkshopRepository
from app.models.domain import WorkshopRecord


def test_legacy_snapshot_does_not_block_worker_revoke_or_admin_correction(
    tmp_path,
) -> None:
    repository = WorkshopRepository(tmp_path / "metadata.db")
    repository.initialize()
    confirmed_at = "2026-09-09T16:15:00+08:00"
    admin = repository.bootstrap_admin(
        username="admin",
        display_name="管理员",
        password="admin-password",
        now=confirmed_at,
    )
    worker = repository.create_user(
        username="worker01",
        display_name="汪磊",
        password="worker-password",
        role="worker",
        now=confirmed_at,
    )
    legacy_report = repository.create_daily_report(
        report_date="2026-09-09",
        source_mode="sqlserver",
        records=[
            WorkshopRecord(
                task_id="legacy-task",
                project_code="W232",
                ship_no="W232",
                segment_name="P100",
                program_no="W232P100A001.gen",
                cut_length=Decimal("1"),
                groove_length=Decimal("0"),
                work_time=Decimal("120"),
                production_time=datetime.fromisoformat(confirmed_at),
            )
        ],
        user=admin,
        now=confirmed_at,
        scope_machine_no=None,
    )
    repository.confirm_task(
        task_id="legacy-task",
        report_id=legacy_report["report_id"],
        user=worker,
        now=confirmed_at,
    )

    app = FastAPI()
    app.include_router(router)
    app.state.workshop = repository
    app.dependency_overrides[require_worker] = lambda: worker
    app.dependency_overrides[require_admin] = lambda: admin

    @app.middleware("http")
    async def add_request_id(request, call_next):
        request.state.request_id = "test"
        return await call_next(request)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/workshop/tasks/legacy-task/revoke",
            json={"report_date": "2026-09-09", "reason": "误点了其他任务"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["action"] == "worker_revoke"
        assert response.json()["data"]["report_id"] == legacy_report["report_id"]

        repository.confirm_task(
            task_id="legacy-task",
            report_id=legacy_report["report_id"],
            user=worker,
            now="2026-09-09T16:20:00+08:00",
        )
        response = client.post(
            "/api/v1/workshop/tasks/legacy-task/correct",
            json={
                "report_date": "2026-09-09",
                "action": "revoke",
                "reason": "根据现场记录撤销确认",
            },
        )
        assert response.status_code == 200
        assert response.json()["data"]["action"] == "admin_revoke"
        assert response.json()["data"]["report_id"] == legacy_report["report_id"]

    assert repository.get_confirmations(["legacy-task"]) == {}

