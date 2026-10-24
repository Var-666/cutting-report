from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from time import time
from typing import Iterator


class ExecutionLeaseLost(RuntimeError):
    pass


_execution: ContextVar[tuple | None] = ContextVar("sqlite_execution_lease", default=None)


@contextmanager
def execution_scope(path: Path, name: str, owner: str):
    token = _execution.set((path.resolve(), name, owner))
    try:
        yield
    finally:
        _execution.reset(token)


def execution_owner() -> str | None:
    current = _execution.get()
    return current[2] if current else None


def require_execution_lease(connection: sqlite3.Connection, path: Path) -> None:
    current = _execution.get()
    if current is None or current[0] != path.resolve():
        return
    row = connection.execute(
        "SELECT 1 FROM process_leases WHERE name=? AND owner=? AND expires_at>?",
        (current[1], current[2], time()),
    ).fetchone()
    if row is None:
        raise ExecutionLeaseLost("后台执行租约已失效，拒绝旧执行者读取或提交")


def require_worker_recovery(connection: sqlite3.Connection) -> None:
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='process_leases'").fetchone() is None:
        return
    current = _execution.get()
    if current is None or current[1] != 'worker' or connection.execute(
        "SELECT 1 FROM process_leases WHERE name='worker' AND owner=? AND expires_at>?", (current[2], time())
    ).fetchone() is None:
        raise ExecutionLeaseLost("只有持有有效租约的 Worker 可以恢复后台运行态")


class SQLiteDatabase:
    """Own SQLite connection policy for every repository in the application."""

    def __init__(self, path: Path, *, timeout_seconds: int = 30) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=self.timeout_seconds)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(f"PRAGMA busy_timeout={self.timeout_seconds * 1000}")
        try:
            require_execution_lease(connection, self.path)
            yield connection
            # Writes hold SQLite's transaction lock through this check and commit.
            if connection.in_transaction:
                require_execution_lease(connection, self.path)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class SQLiteRepository:
    """Base class that supports standalone and Unit-of-Work-bound repositories."""

    def __init__(
        self,
        database: Path | SQLiteDatabase,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        self.database = (
            database if isinstance(database, SQLiteDatabase) else SQLiteDatabase(database)
        )
        self.database_path = self.database.path
        self._connection = connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self._connection is not None:
            yield self._connection
            return
        with self.database.connect() as connection:
            yield connection

    @staticmethod
    def begin_immediate(connection: sqlite3.Connection) -> None:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
