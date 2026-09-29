from __future__ import annotations

import sqlite3
from pathlib import Path

from app.database.sqlite import SQLiteDatabase, SQLiteRepository


class LlmUsageRepository(SQLiteRepository):
    def __init__(
        self,
        database: Path | SQLiteDatabase,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        super().__init__(database, connection=connection)

    def initialize(self) -> None:
        self.database.initialize()
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS llm_usage_totals (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    call_count INTEGER NOT NULL DEFAULT 0,
                    metered_count INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute("INSERT OR IGNORE INTO llm_usage_totals(id) VALUES (1)")

    def record_call(self, total_tokens: int | None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE llm_usage_totals
                SET call_count = call_count + 1,
                    metered_count = metered_count + ?,
                    total_tokens = total_tokens + ?
                WHERE id = 1
                """,
                (int(total_tokens is not None), total_tokens or 0),
            )

    def summary(self) -> dict[str, int]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT call_count, metered_count, total_tokens FROM llm_usage_totals WHERE id = 1"
            ).fetchone()
        return dict(row) if row else {"call_count": 0, "metered_count": 0, "total_tokens": 0}
