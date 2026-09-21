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
    registry.activate("target")
    registry.activate("other")
    registry.register("target", first)
    registry.register("target", second)
    registry.register("other", unrelated)

    assert registry.cancel("target") is True
    assert first.cancel_calls == 1
    assert second.cancel_calls == 1
    assert unrelated.cancel_calls == 0
    assert registry.is_cancelled("target") is True


def test_cursor_registered_after_cancel_is_rejected_and_closed() -> None:
    request_id = "test_cancel_before_cursor"
    cursor = _Cursor()
    sql_request_cancellations.activate(request_id)
    sql_request_cancellations.cancel(request_id)
    token = bind_request_id(request_id)
    try:
        with pytest.raises(SqlRequestCancelled):
            with cancellable_cursor(_Connection(cursor)):
                pytest.fail("cancelled SQL must not execute")
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(request_id)

    assert cursor.cancel_calls == 1
    assert cursor.closed is True


def test_driver_error_after_cancel_becomes_request_cancelled() -> None:
    request_id = "test_cancel_during_cursor"
    cursor = _Cursor()
    sql_request_cancellations.activate(request_id)
    token = bind_request_id(request_id)
    try:
        with pytest.raises(SqlRequestCancelled):
            with cancellable_cursor(_Connection(cursor)):
                sql_request_cancellations.cancel(request_id)
                raise RuntimeError("driver interrupted")
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(request_id)

    assert cursor.cancel_calls == 1
    assert cursor.closed is True


def test_cancelled_cursor_cannot_return_a_late_success() -> None:
    request_id = "test_cancel_late_success"
    cursor = _Cursor()
    sql_request_cancellations.activate(request_id)
    token = bind_request_id(request_id)
    try:
        with pytest.raises(SqlRequestCancelled):
            with cancellable_cursor(_Connection(cursor)):
                sql_request_cancellations.cancel(request_id)
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(request_id)

    assert cursor.cancel_calls == 1
    assert cursor.closed is True


def test_http_request_id_reaches_sync_query_thread(monkeypatch) -> None:
    class _QueryService:
        seen_request_id: str | None = None

        def fetch_equipment_data(self, _filters):
            self.seen_request_id = current_request_id.get()
            return SimpleNamespace(records=[])

    query_service = _QueryService()
    monkeypatch.setattr(app.state, "query_service", query_service, raising=False)
    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    try:
        response = TestClient(app).post(
            "/api/v1/database/preview",
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
    assert query_service.seen_request_id == "test_context_propagation"
    assert response.headers["X-Request-ID"] == "test_context_propagation"


def test_cancel_endpoint_interrupts_registered_cursor() -> None:
    request_id = "test_endpoint_cancel"
    cursor = _Cursor()
    sql_request_cancellations.activate(request_id)
    sql_request_cancellations.register(request_id, cursor)
    app.dependency_overrides[require_user] = lambda: {"role": "admin"}
    try:
        response = TestClient(app).post(f"/api/v1/requests/{request_id}/cancel")
    finally:
        app.dependency_overrides.pop(require_user, None)
        sql_request_cancellations.finish(request_id)

    assert response.status_code == 200
    assert response.json()["data"] == {"accepted": True}
    assert cursor.cancel_calls == 1
