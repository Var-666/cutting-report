from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.auth import require_admin, require_user
from app.main import app
from app.services.request_cancellation import (
    SqlRequestCancellationRegistry,
    SqlRequestCancelled,
    bind_request_id,
    cancellation_owner,
    cancellation_session_scope,
    cancellable_cursor,
    current_request_id,
    reset_request_id,
    sql_request_cancellations,
)


class _Cursor:
    def __init__(self) -> None:
        self.cancel_calls = 0
        self.closed = False

    def cancel(self) -> None:
        self.cancel_calls += 1

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


def test_registry_cancels_all_cursors_for_only_the_target_request() -> None:
    registry = SqlRequestCancellationRegistry()
    first = _Cursor()
    second = _Cursor()
    unrelated = _Cursor()
    target = registry.activate("target")
    other = registry.activate("other")
    registry.register(target, first)
    registry.register(target, second)
    registry.register(other, unrelated)

    assert registry.cancel(target) is True
    assert first.cancel_calls == 1
    assert second.cancel_calls == 1
    assert unrelated.cancel_calls == 0
    assert registry.is_cancelled(target) is True


def test_cursor_registered_after_cancel_is_rejected_and_closed() -> None:
    request_id = "test_cancel_before_cursor"
    cursor = _Cursor()
    execution_id = sql_request_cancellations.activate(request_id)
    sql_request_cancellations.cancel(execution_id)
    token = bind_request_id(execution_id)
    try:
        with pytest.raises(SqlRequestCancelled):
            with cancellable_cursor(_Connection(cursor)):
                pytest.fail("cancelled SQL must not execute")
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution_id)

    assert cursor.cancel_calls == 1
    assert cursor.closed is True


def test_driver_error_after_cancel_becomes_request_cancelled() -> None:
    request_id = "test_cancel_during_cursor"
    cursor = _Cursor()
    execution_id = sql_request_cancellations.activate(request_id)
    token = bind_request_id(execution_id)
    try:
        with pytest.raises(SqlRequestCancelled):
            with cancellable_cursor(_Connection(cursor)):
                sql_request_cancellations.cancel(execution_id)
                raise RuntimeError("driver interrupted")
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution_id)

    assert cursor.cancel_calls == 1
    assert cursor.closed is True


def test_cancelled_cursor_cannot_return_a_late_success() -> None:
    request_id = "test_cancel_late_success"
    cursor = _Cursor()
    execution_id = sql_request_cancellations.activate(request_id)
    token = bind_request_id(execution_id)
    try:
        with pytest.raises(SqlRequestCancelled):
            with cancellable_cursor(_Connection(cursor)):
                sql_request_cancellations.cancel(execution_id)
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution_id)

    assert cursor.cancel_calls == 1
    assert cursor.closed is True


def test_http_request_id_reaches_sync_query_thread(monkeypatch) -> None:
    class _QueryService:
        seen_request_id: str | None = None

        def fetch_equipment_data(self, _filters):
            self.seen_request_id = current_request_id.get()
            return SimpleNamespace(records=[])

    query_service = _QueryService()
    monkeypatch.setattr(app.state, "devices", {"Cutter01": SimpleNamespace(query_service=query_service)}, raising=False)
    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    try:
        response = TestClient(app).post(
            "/api/v1/database/preview?device_id=Cutter01",
            headers={"X-Request-ID": "test_context_propagation"},
            json={
                "dataset": "equipment",
                "report_date": "2026-09-21",
                "limit": 10,
            },
        )
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert response.status_code == 200
    assert query_service.seen_request_id
    assert query_service.seen_request_id != "test_context_propagation"
    assert response.headers["X-Request-ID"] == "test_context_propagation"


def test_cancel_endpoint_interrupts_registered_cursor() -> None:
    request_id = "test_endpoint_cancel"
    cursor = _Cursor()
    execution_id = sql_request_cancellations.activate(request_id)
    sql_request_cancellations.register(execution_id, cursor)
    sql_request_cancellations.set_owner(execution_id, cancellation_owner("admin", "test-session"))
    app.dependency_overrides[require_user] = lambda: {"role": "admin", "user_id": "admin"}
    try:
        client = TestClient(app)
        client.cookies.set("cutting_session", "test-session")
        response = client.post(f"/api/v1/requests/{request_id}/cancel")
    finally:
        app.dependency_overrides.pop(require_user, None)
        sql_request_cancellations.finish(execution_id)

    assert response.status_code == 200
    assert response.json()["data"] == {"accepted": True}
    assert cursor.cancel_calls == 1


def test_registry_separates_identical_client_ids_and_finish_lifetimes():
    registry = SqlRequestCancellationRegistry()
    first = registry.activate("shared")
    second = registry.activate("shared")
    assert first != second
    assert registry.set_owner(first, cancellation_owner("u1", "session1"))
    assert registry.set_owner(second, cancellation_owner("u2", "session2"))
    first_cursor, second_cursor = _Cursor(), _Cursor()
    registry.register(first, first_cursor)
    registry.register(second, second_cursor)
    assert not registry.cancel_owned("shared", cancellation_owner("u1", "other-session"))
    registry.finish(first)
    assert registry.cancel_owned("shared", cancellation_owner("u2", "session2"))
    assert first_cursor.cancel_calls == 0
    assert second_cursor.cancel_calls == 1
    registry.finish(second)
    assert registry._requests == {}


def test_same_session_duplicate_client_id_is_rejected_without_stealing_first_request():
    registry = SqlRequestCancellationRegistry()
    owner = cancellation_owner("u1", "session1")
    first = registry.activate("shared")
    second = registry.activate("shared")
    assert registry.set_owner(first, owner)
    assert not registry.set_owner(second, owner)
    registry.finish(second)
    assert registry.cancel_owned("shared", owner)
    assert registry.is_cancelled(first)


@pytest.mark.parametrize("foreign_session", ["user2-session", "user1-other-session"])
def test_real_http_auth_binds_cancel_to_user_and_session(monkeypatch, foreign_session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    started, release = Event(), Event()
    cursor = _Cursor()

    def cancel():
        cursor.cancel_calls += 1
        release.set()

    cursor.cancel = cancel

    class Workshop:
        def user_for_session(self, token, **kwargs):
            if token == "user2-session":
                return {"user_id": "user2", "role": "admin"}
            if token in {"user1-session", "user1-other-session"}:
                return {"user_id": "user1", "role": "admin"}
            return None

    class Query:
        def fetch_equipment_data(self, filters):
            with cancellable_cursor(_Connection(cursor)):
                started.set()
                assert release.wait(3)
                return SimpleNamespace(records=[])

    monkeypatch.setattr(app.state, "workshop", Workshop(), raising=False)
    monkeypatch.setattr(app.state, "devices", {"Cutter01": SimpleNamespace(query_service=Query())}, raising=False)
    own = TestClient(app)
    own.cookies.set("cutting_session", "user1-session")
    foreign = TestClient(app)
    foreign.cookies.set("cutting_session", foreign_session)
    duplicate = TestClient(app)
    duplicate.cookies.set("cutting_session", "user1-session")
    payload = {"dataset": "equipment", "report_date": "2026-09-21", "limit": 10}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(own.post, "/api/v1/database/preview?device_id=Cutter01",
                              headers={"X-Request-ID": "owned-query"}, json=payload)
        try:
            assert started.wait(1)
            denied = foreign.post("/api/v1/requests/owned-query/cancel")
            assert denied.status_code == 200  # Do not reveal another session's active requests.
            assert denied.json()["data"] == {"accepted": True}
            assert cursor.cancel_calls == 0
            collision = duplicate.post("/api/v1/database/preview?device_id=Cutter01",
                                       headers={"X-Request-ID": "owned-query"}, json=payload)
            assert collision.status_code == 409
            accepted = own.post("/api/v1/requests/owned-query/cancel")
            assert accepted.status_code == 200
            assert cursor.cancel_calls == 1
            assert pending.result(timeout=2).status_code == 499
        finally:
            release.set()
    assert cursor.closed
    assert not any(state["client_request_id"] == "owned-query"
                   for state in sql_request_cancellations._requests.values())


def test_authenticated_cancel_before_query_auth_is_confined_to_session():
    registry = SqlRequestCancellationRegistry()
    execution = registry.activate("early", cancellation_session_scope("session1"))
    assert not registry.cancel_owned("early", cancellation_owner("u2", "session2"),
                                     cancellation_session_scope("session2"))
    assert registry.cancel_owned("early", cancellation_owner("u1", "session1"),
                                 cancellation_session_scope("session1"))
    assert registry.set_owner(execution, cancellation_owner("u1", "session1"))
    cursor = _Cursor()
    assert registry.register(execution, cursor), "early cancellation must reject the eventual SQL"
    assert registry.is_cancelled(execution)


def test_unauthenticated_cancel_cannot_use_pending_session_scope():
    registry = SqlRequestCancellationRegistry()
    execution = registry.activate("early", cancellation_session_scope("session1"))
    assert not registry.cancel_owned("early", None, cancellation_session_scope("session1"))
    assert not registry.is_cancelled(execution)
