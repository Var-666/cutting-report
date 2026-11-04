"""Frozen SQLite upgrade support, used only before the offline PostgreSQL import."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Iterator


class SQLiteHistory:
    """Offline SQLite history upgrades only; never imported by Web/Worker."""

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


def backup_before_process_upgrade(database) -> None:
    if not database.path.is_file():
        return
    with database.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "report_jobs" not in tables or "process_leases" in tables:
            return
        directory = database.path.parent / "backups"
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{database.path.stem}-before-processes-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db"
        destination = sqlite3.connect(target)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        os.chmod(target, 0o600)
