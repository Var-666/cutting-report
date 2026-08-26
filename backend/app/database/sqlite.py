from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


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
            yield connection
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
