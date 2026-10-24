from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_user
from app.api.workloads import authentication_requests
from app.database.workshop import WorkshopRepository
from app.services.request_cancellation import SqlRequestCancelled
from app.services.worker_auth_service import WorkerAuthService
from test_worker_auth_service import FakeSqlServerClient, _enabled_settings, _valid_row


@pytest.fixture
def hr_sessions(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("app.services.worker_auth_service.monotonic", lambda: clock[0])
    repository = WorkshopRepository(tmp_path / "metadata.db")
    repository.initialize()
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    user = repository.upsert_external_worker(external_id="9123", company_no="C0007",
                                             group_no="G0021", display_name="张三", now=now.isoformat())
    first, _ = repository.create_session(user["user_id"], now=now, hours=1)
    second, _ = repository.create_session(user["user_id"], now=now, hours=1)
    source = FakeSqlServerClient([_valid_row()])
    auth = WorkerAuthService(_enabled_settings(), source)
    app = FastAPI()
    app.state.workshop = repository
    app.state.worker_auth = auth
    writes = []

    @app.get("/protected")
    def read(user=Depends(require_user)):
        return {"user_id": user["user_id"]}

    @app.post("/protected")
    def write(user=Depends(require_user)):
        writes.append(user["user_id"])
        return {"ok": True}

    client = TestClient(app)
    client.cookies.set("cutting_session", first)
    return client, repository, source, auth, clock, first, second, writes


def test_disabled_hr_identity_revokes_every_existing_session_after_read_ttl(hr_sessions):
    client, repository, source, auth, clock, first, second, _ = hr_sessions
    assert client.get("/protected").status_code == 200
    source.rows = [_valid_row(available_value=0)]
    assert client.get("/protected").status_code == 200  # The bounded read-cache window is explicit.
    assert len(source.calls) == 1
    clock[0] += 61
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.headers["X-Session-Expired"] == "1"
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    assert repository.user_for_session(first, now=stamp) is None
    assert repository.user_for_session(second, now=stamp) is None


def test_write_rechecks_even_fresh_positive_cache_and_cannot_mutate_when_disabled(hr_sessions):
    client, _, source, _, _, _, _, writes = hr_sessions
    assert client.get("/protected").status_code == 200
    source.rows = [_valid_row(available_value=0)]
    assert client.post("/protected").status_code == 401
    assert writes == []
    assert len(source.calls) == 2


def test_hr_outage_denies_request_but_preserves_session_for_recovery(hr_sessions):
    client, repository, source, auth, clock, first, _, writes = hr_sessions
    assert client.get("/protected").status_code == 200
    source.error = RuntimeError("HR unavailable")
    response = client.post("/protected")
    assert response.status_code == 503
    assert "X-Session-Expired" not in response.headers
    assert writes == []
    assert repository.user_for_session(first, now=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat())
    calls = len(source.calls)
    assert client.post("/protected").status_code == 503
    assert len(source.calls) == calls, "failure cooldown must bound repeat write checks"
    source.error = None
    clock[0] += 16
    assert client.get("/protected").status_code == 200


def test_local_admin_session_does_not_depend_on_hr_availability(hr_sessions):
    client, repository, source, _, _, _, _, _ = hr_sessions
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    user = repository.create_user(username="localadmin", display_name="本地管理员",
                                  password="test-password", role="admin", now=now.isoformat())
    token, _ = repository.create_session(user["user_id"], now=now, hours=1)
    client.cookies.set("cutting_session", token)
    source.error = RuntimeError("HR unavailable")
    assert client.post("/protected").status_code == 200
    assert source.calls == []


def test_hr_session_check_cache_has_capacity_bound():
    source = FakeSqlServerClient([_valid_row()])
    auth = WorkerAuthService(_enabled_settings(), source)
    for index in range(300):
        assert auth.session_identity_available(str(index))
    assert len(auth._session_checks) == 256
    assert "0" not in auth._session_checks


def test_cancelled_hr_check_propagates_without_outage_cache():
    auth = WorkerAuthService(_enabled_settings(), FakeSqlServerClient(error=SqlRequestCancelled("cancelled")))
    with pytest.raises(SqlRequestCancelled):
        auth.session_identity_available("9123")
    assert not auth._session_checks


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 3601])
def test_revalidation_interval_rejects_invalid_limits(value):
    with pytest.raises(ValueError, match="WORKER_DATABASE_SESSION_CHECK_SECONDS"):
        _enabled_settings(worker_database_session_check_seconds=value)


def test_successful_login_replaces_previous_disabled_cache():
    source = FakeSqlServerClient([_valid_row(available_value=0)])
    auth = WorkerAuthService(_enabled_settings(), source)
    assert not auth.session_identity_available("9123")
    source.rows = [_valid_row()]
    assert auth.authenticate("C0007", "password")["external_id"] == "9123"
    assert auth.session_identity_available("9123")
    assert len(source.calls) == 2


@pytest.mark.parametrize(("cached", "expected"), [(True, 200), (False, 401), (None, 503)])
def test_cached_hr_decisions_bypass_full_authentication_capacity(hr_sessions, cached, expected):
    client, repository, source, service, _, first, second, _ = hr_sessions
    service._cache_session_check("9123", cached, 60 if cached is not None else 15)
    for _ in range(8):
        assert authentication_requests._slots.acquire(blocking=False)
    try:
        response = client.get("/protected")
        assert response.status_code == expected
        assert source.calls == []
        if cached is None:
            assert "Retry-After" not in response.headers
            assert "X-Session-Expired" not in response.headers
        if cached is False:
            stamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            assert repository.user_for_session(first, now=stamp) is None
            assert repository.user_for_session(second, now=stamp) is None
    finally:
        for _ in range(8):
            authentication_requests._slots.release()
