from __future__ import annotations

import logging
import threading
from hashlib import sha256
from uuid import uuid4
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator


logger = logging.getLogger(__name__)


class SqlRequestCancelled(RuntimeError):
    """Raised when an HTTP request has cancelled its active SQL operation."""


class SqlRequestCancellationRegistry:
    """Track SQL cursors that belong to currently active HTTP requests."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, dict[str, Any]] = {}

    def activate(self, client_request_id: str, session_scope: str | None = None) -> str:
        execution_id = uuid4().hex
        with self._lock:
            self._requests[execution_id] = {
                "client_request_id": client_request_id, "owner": None, "session_scope": session_scope,
                "cancelled": False, "cursors": {},
            }
        return execution_id

    def set_owner(self, execution_id: str | None, owner: str | None) -> bool:
        if execution_id is None or owner is None:
            return True
        with self._lock:
            state = self._requests.get(execution_id)
            if state is None:
                return False
            if state["owner"] is not None:
                return state["owner"] == owner
            if any(other_id != execution_id and other["owner"] == owner
                   and other["client_request_id"] == state["client_request_id"]
                   for other_id, other in self._requests.items()):
                return False
            state["owner"] = owner
            return True

    def cancel_owned(self, client_request_id: str, owner: str | None,
                     session_scope: str | None = None) -> bool:
        if owner is None:
            return False
        with self._lock:
            execution_ids = [execution_id for execution_id, state in self._requests.items()
                             if (state["owner"] == owner or (state["owner"] is None
                                 and session_scope is not None and state["session_scope"] == session_scope))
                             and state["client_request_id"] == client_request_id]
        results = [self.cancel(execution_id) for execution_id in execution_ids]
        return any(results)

    def finish(self, request_id: str) -> None:
        with self._lock:
            self._requests.pop(request_id, None)

    def register(self, request_id: str | None, cursor: Any) -> bool:
        if not request_id:
            return False
        with self._lock:
            state = self._requests.get(request_id)
            if state is None:
                return False
            state["cursors"][id(cursor)] = cursor
            return bool(state["cancelled"])

    def unregister(self, request_id: str | None, cursor: Any) -> None:
        if not request_id:
            return
        with self._lock:
            state = self._requests.get(request_id)
            if state is not None:
                state["cursors"].pop(id(cursor), None)

    def is_cancelled(self, request_id: str | None) -> bool:
        if not request_id:
            return False
        with self._lock:
            state = self._requests.get(request_id)
            return bool(state and state["cancelled"])

    def cancel(self, request_id: str) -> bool:
        with self._lock:
            state = self._requests.get(request_id)
            if state is None:
                return False
            state["cancelled"] = True
            cursors = tuple(state["cursors"].values())

        # pyodbc supports cancelling a cursor from another thread. Do not hold
        # the registry lock while invoking the driver because cancellation can
        # wait briefly for the ODBC driver.
        for cursor in cursors:
            try:
                cursor.cancel()
            except Exception:
                logger.debug(
                    "failed to cancel SQL cursor for request %s",
                    request_id,
                    exc_info=True,
                )
        return True


def cancellation_session_scope(session_token: str | None) -> str | None:
    return sha256(session_token.encode()).hexdigest() if session_token else None


def cancellation_owner(user_id: str | None, session_token: str | None) -> str | None:
    if not user_id or not session_token:
        return None
    return sha256(f"{user_id}:{session_token}".encode()).hexdigest()


sql_request_cancellations = SqlRequestCancellationRegistry()
current_request_id: ContextVar[str | None] = ContextVar(
    "current_request_id",
    default=None,
)


def bind_request_id(request_id: str) -> Token[str | None]:
    return current_request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    current_request_id.reset(token)


@contextmanager
def cancellable_cursor(connection: Any) -> Iterator[Any]:
    """Create a cursor that the matching HTTP cancellation request can stop."""
    cursor = connection.cursor()
    request_id = current_request_id.get()
    already_cancelled = sql_request_cancellations.register(request_id, cursor)
    try:
        if already_cancelled:
            try:
                cursor.cancel()
            finally:
                raise SqlRequestCancelled("SQL 查询已取消")
        yield cursor
        if sql_request_cancellations.is_cancelled(request_id):
            raise SqlRequestCancelled("SQL 查询已取消")
    except Exception as exc:
        if not isinstance(exc, SqlRequestCancelled) and sql_request_cancellations.is_cancelled(
            request_id
        ):
            raise SqlRequestCancelled("SQL 查询已取消") from exc
        raise
    finally:
        sql_request_cancellations.unregister(request_id, cursor)
        try:
            cursor.close()
        except Exception:
            logger.debug("failed to close SQL cursor", exc_info=True)
