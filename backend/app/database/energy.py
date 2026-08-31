from __future__ import annotations

import json

from app.database.sqlite import SQLiteRepository


class EnergyReportRepository(SQLiteRepository):
    """Persist hourly/day results, isolated by meter set and calculation settings."""

    def initialize(self):
        self.database.initialize()
        with self.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS energy_daily_snapshots (
                    source_key TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    report_json TEXT NOT NULL,
                    PRIMARY KEY (source_key, report_date)
                )
            """)

    def get_day(self, source_key, report_date):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT report_json FROM energy_daily_snapshots WHERE source_key = ? AND report_date = ?",
                (source_key, report_date),
            ).fetchone()
        return json.loads(row["report_json"]) if row else None

    def list_month(self, source_key, report_month):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT report_json FROM energy_daily_snapshots WHERE source_key = ? AND report_date LIKE ? ORDER BY report_date",
                (source_key, report_month + "-%"),
            ).fetchall()
        return [json.loads(row["report_json"]) for row in rows]

    def save_day(self, source_key, report):
        with self.connect() as connection:
            connection.execute("""
                INSERT INTO energy_daily_snapshots (source_key, report_date, updated_at, report_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (source_key, report_date) DO UPDATE SET
                    updated_at = excluded.updated_at, report_json = excluded.report_json
            """, (source_key, report["report_date"], report["updated_at"],
                  json.dumps(report, ensure_ascii=False, allow_nan=False)))
