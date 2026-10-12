from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import optional_user
from app.api.llm_usage import router
from app.config import Settings
from app.database.llm_usage import LlmUsageRepository
from app.services.llm_usage_service import record_llm_call


@pytest.fixture
def repository(tmp_path):
    repository = LlmUsageRepository(tmp_path / "usage.db")
    repository.initialize()
    with repository.connect() as connection:
        connection.execute("UPDATE llm_usage_tracking SET started_at = '2026-01-01T00:00:00+08:00'")
    return repository


def test_daily_and_monthly_totals_include_failed_and_unmetered_calls_without_inventing_usage(repository):
    for stamp, tokens, scene in [("2026-02-28", 12, "alarm"), ("2026-03-01", 30, "workshop")]:
        repository.record_call(tokens, occurred_at=datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo("Asia/Shanghai")),
                               scene=scene, model="model-a", input_tokens=tokens - 2, output_tokens=2)
    repository.record_call(None, occurred_at=datetime(2026, 3, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
                           scene="alarm", status="failed")
    daily = repository.report(date(2026, 2, 28), date(2026, 3, 2))
    monthly = repository.report(date(2026, 2, 28), date(2026, 3, 2), granularity="month")
    assert [row["total_tokens"] for row in daily["series"]] == [12, 30, 0]
    assert [row["total_tokens"] for row in monthly["series"]] == [12, 30]
    assert daily["summary"] == monthly["summary"]
    assert daily["summary"]["failed_count"] == 1
    assert daily["summary"]["call_count"] == 3
    assert daily["summary"]["metered_count"] == 2
    alarm = repository.report(date(2026, 3, 1), date(2026, 3, 2), scene="alarm")
    assert alarm["summary"]["total_tokens"] is None
    assert alarm["series"][0]["input_tokens"] is None
    assert alarm["series"][1]["total_tokens"] == 0
    assert alarm["calls"]["total"] == 1


def test_upgrade_preserves_legacy_totals_without_assigning_them_to_today(tmp_path):
    repository = LlmUsageRepository(tmp_path / "old.db")
    with repository.connect() as connection:
        connection.execute("CREATE TABLE llm_usage_totals (id INTEGER PRIMARY KEY, call_count INTEGER, metered_count INTEGER, total_tokens INTEGER)")
        connection.execute("INSERT INTO llm_usage_totals VALUES (1, 10, 8, 700)")
    repository.initialize()
    repository.initialize()
    result = repository.report(date(2020, 1, 1), date(2020, 1, 2))
    assert result["legacy"] == result["lifetime"] == {"call_count": 10, "metered_count": 8, "total_tokens": 700}
    assert result["summary"]["call_count"] == 0
    assert result["tracking_started_at"].endswith("+08:00")
    assert all(row["before_tracking"] and row["total_tokens"] is None for row in result["series"])


def test_call_and_lifetime_are_atomic_and_concurrent_calls_are_not_lost(repository):
    with repository.connect() as connection:
        connection.execute("CREATE TRIGGER fail_totals BEFORE UPDATE ON llm_usage_totals BEGIN SELECT RAISE(ABORT, 'write failed'); END")
    with pytest.raises(Exception, match="write failed"):
        repository.record_call(5)
    with repository.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM llm_usage_calls").fetchone()[0] == 0
        connection.execute("DROP TRIGGER fail_totals")
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(repository.record_call, [5] * 24))
    assert repository.summary() == {"call_count": 24, "metered_count": 24, "total_tokens": 120}


def test_accounting_uses_business_timezone_and_does_not_double_count_token_subsets(tmp_path):
    settings = Settings(project_root=tmp_path, timezone="Asia/Shanghai", llm_model="model-a")
    stamp = datetime(2026, 2, 28, 16, 1, tzinfo=timezone.utc)
    record_llm_call(settings, object(), {"usage": {
        "prompt_tokens": 20, "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 12},
        "completion_tokens_details": {"reasoning_tokens": 3},
    }}, scene="alarm", occurred_at=stamp, duration_ms=250)
    result = LlmUsageRepository(settings.metadata_db_path).report(date(2026, 3, 1), date(2026, 3, 1))
    row = result["calls"]["items"][0]
    assert row["total_tokens"] == 30
    assert (row["input_tokens"], row["output_tokens"], row["cached_tokens"], row["reasoning_tokens"]) == (20, 10, 12, 3)
    assert row["scene"] == "alarm" and row["model"] == "model-a"
    assert row["occurred_at"] == "2026-03-01T00:01:00+08:00"
    assert row["duration_ms"] == 250


@pytest.mark.parametrize("usage", [None, {"total_tokens": True}, {"total_tokens": -1}, {"prompt_tokens": 3}, {"total_tokens": "20"}])
def test_invalid_or_partial_token_counts_stay_unknown(tmp_path, usage):
    settings = Settings(project_root=tmp_path)
    record_llm_call(settings, object(), {"usage": usage})
    repository = LlmUsageRepository(settings.metadata_db_path)
    with repository.connect() as connection:
        row = connection.execute("SELECT * FROM llm_usage_calls").fetchone()
        assert row["total_tokens"] is None
    assert repository.summary()["metered_count"] == 0


def test_pagination_is_stable_and_summary_does_not_change_with_page(repository):
    for index in range(23):
        repository.record_call(index, occurred_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
    first = repository.report(date(2026, 3, 1), date(2026, 3, 1))
    last = repository.report(date(2026, 3, 1), date(2026, 3, 1), page=999)
    assert last["calls"]["page"] == 2
    assert len(last["calls"]["items"]) == 3
    assert first["calls"]["items"][0]["total_tokens"] == 22
    assert first["summary"] == last["summary"]
    assert not ({row["id"] for row in first["calls"]["items"]} & {row["id"] for row in last["calls"]["items"]})


def test_usage_api_is_admin_only_bounded_and_exposes_no_provider_credentials(repository):
    app = FastAPI()
    app.state.llm_usage = repository
    app.include_router(router)

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test"
        return await call_next(request)

    path = "/api/v1/llm-usage/report?start_date=2026-01-01&end_date=2026-12-31"
    with TestClient(app) as client:
        app.dependency_overrides[optional_user] = lambda: None
        assert client.get(path).status_code == 401
        app.dependency_overrides[optional_user] = lambda: {"role": "worker"}
        assert client.get(path).status_code == 403
        app.dependency_overrides[optional_user] = lambda: {"role": "admin"}
        response = client.get(path)
        assert response.status_code == 200
        assert len(response.json()["data"]["series"]) == 365
        assert not {"api_key", "base_url", "password", "prompt", "response_body"} & response.json()["data"].keys()
        for suffix in ("&granularity=year", "&scene=unknown", "&page=0", "&page_size=101"):
            assert client.get(path + suffix).status_code == 422
        assert client.get("/api/v1/llm-usage/report?start_date=2026-12-31&end_date=2026-01-01").status_code == 422
        assert client.get("/api/v1/llm-usage/report?start_date=2025-01-01&end_date=2026-12-31").status_code == 422
