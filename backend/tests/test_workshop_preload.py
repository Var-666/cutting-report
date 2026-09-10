from datetime import date
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.auth import require_user
from app.api.workshop import router
from app.config import Settings
from app.database.workshop import WorkshopRepository
from app.models.domain import QueryBundle
from app.services.query_service import QueryService


@pytest.fixture
def service(tmp_path):
    return QueryService(Settings(project_root=tmp_path, data_source_mode="demo", demo_mode=True))


def test_workshop_warmup_and_report_generation_share_query_without_confirming(service, tmp_path, monkeypatch):
    repo = WorkshopRepository(tmp_path / "metadata.db")
    repo.initialize()
    admin = repo.bootstrap_admin(username="testadmin", display_name="测试管理员",
                                 password="test-only-password", now="2026-09-03T08:00:00+08:00")
    app = FastAPI()
    app.include_router(router)
    app.state.workshop = repo
    app.state.query_service = service
    app.dependency_overrides[require_user] = lambda: admin

    @app.middleware("http")
    async def add_request_id(request, call_next):
        request.state.request_id = "test"
        return await call_next(request)

    calls = []
    fetch = service.fetch_report_data

    def spy(filters):
        calls.append(filters.report_date)
        return fetch(filters)

    monkeypatch.setattr(service, "fetch_report_data", spy)
    with TestClient(app) as client:
        query = {"report_date": "2026-09-03", "warmup": "true"}
        response = client.get("/api/v1/workshop/tasks", params=query)
        assert response.status_code == 200
        assert response.json()["data"]["report"] is None
        assert repo.get_daily_report("2026-09-03") is None
        assert len(calls) == 1
        response = client.post("/api/v1/workshop/reports", json={"report_date": "2026-09-03"})
        assert response.status_code == 200
        assert response.json()["data"]["item_count"] > 0
        assert len(calls) == 1
        response = client.get("/api/v1/workshop/tasks", params=query)
        data = response.json()["data"]
        assert len(calls) == 1
        assert data["total"] > 0
        assert data["confirmed_count"] == 0
        assert data["worker_summaries"] == []
        assert all(not row["confirmed_by"] for row in data["items"])

        worker = repo.upsert_external_worker(
            external_id="hr-worker-1",
            company_no="C0018",
            group_no="G0018",
            display_name="张三",
            now="2026-09-03T09:00:00+08:00",
        )
        repo.confirm_task(
            task_id=data["items"][0]["task_id"],
            report_id=data["report"]["report_id"],
            user=worker,
            now="2026-09-03T09:05:00+08:00",
        )
        response = client.get("/api/v1/workshop/tasks", params=query)
        worker_summary = response.json()["data"]["worker_summaries"]
        assert worker_summary[0]["company_no"] == "C0018"
        assert worker_summary[0]["worker_name"] == "张三"
        assert worker_summary[0]["confirmed_task_count"] == 1


def test_warmup_cache_expires_and_separates_dates(service, monkeypatch):
    clock = [0]
    monkeypatch.setattr("app.services.query_service.monotonic", lambda: clock[0])
    first_date, second_date = date(2026, 9, 2), date(2026, 9, 3)
    first = service.prepare_workshop_data(first_date)
    assert service.prepare_workshop_data(first_date) is first
    assert service.prepare_workshop_data(second_date) is not first
    clock[0] = 61
    assert service.prepare_workshop_data(first_date) is not first


def test_empty_warmup_does_not_hide_new_tasks(service, monkeypatch):
    results = iter([QueryBundle([], [], []), QueryBundle([], [SimpleNamespace(task_id="new")], [])])
    monkeypatch.setattr(service, "fetch_report_data", lambda filters: next(results))
    target = date(2026, 9, 3)
    assert service.prepare_workshop_data(target).workshop_records == []
    assert service.prepare_workshop_data(target).workshop_records[0].task_id == "new"


def test_failed_warmup_is_retryable(service, monkeypatch):
    calls = []

    def fetch(filters):
        calls.append(filters)
        if len(calls) == 1:
            raise RuntimeError("offline")
        return QueryBundle([], [], [])

    monkeypatch.setattr(service, "fetch_report_data", fetch)
    with pytest.raises(RuntimeError, match="offline"):
        service.prepare_workshop_data(date(2026, 9, 3))
    assert service.prepare_workshop_data(date(2026, 9, 3)).workshop_records == []
