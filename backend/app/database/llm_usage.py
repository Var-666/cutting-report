from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
        from app.database.migrations import apply_migrations
        apply_migrations(self.database, names=("004_llm_usage",))

    def record_call(
        self, total_tokens: int | None, *, occurred_at: datetime | None = None,
        scene: str = "other", model: str = "", status: str = "responded",
        duration_ms: int = 0, input_tokens: int | None = None,
        output_tokens: int | None = None, cached_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> None:
        occurred_at = occurred_at or datetime.now(timezone.utc)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO llm_usage_calls (
                    occurred_at, usage_date, scene, model, status, duration_ms,
                    input_tokens, output_tokens, total_tokens, cached_tokens, reasoning_tokens
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (occurred_at.isoformat(timespec="seconds"), occurred_at.date().isoformat(),
                 scene, model, status, duration_ms, input_tokens, output_tokens,
                 total_tokens, cached_tokens, reasoning_tokens),
            )
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

    @staticmethod
    def _aggregate_columns() -> str:
        # SUM preserves unknown usage; an empty bucket alone has known zero usage.
        tokens = ", ".join(
            f"CASE WHEN COUNT(*) = 0 THEN 0 ELSE SUM({name}) END AS {name}"
            for name in ("input_tokens", "output_tokens", "total_tokens", "cached_tokens", "reasoning_tokens")
        )
        return f"""COUNT(*) AS call_count, COUNT(total_tokens) AS metered_count,
            COUNT(input_tokens) AS input_metered_count,
            COUNT(output_tokens) AS output_metered_count,
            COALESCE(SUM(status = 'failed'), 0) AS failed_count,
            {tokens}, ROUND(AVG(duration_ms)) AS average_duration_ms"""

    def report(
        self, start_date: date, end_date: date, *, granularity: str = "day",
        scene: str = "", page: int = 1, page_size: int = 20,
        timezone_name: str = "Asia/Shanghai",
    ) -> dict:
        where = "usage_date >= ? AND usage_date <= ?"
        parameters: list = [start_date.isoformat(), end_date.isoformat()]
        if scene:
            where += " AND scene = ?"
            parameters.append(scene)
        aggregate = self._aggregate_columns()
        bucket = "usage_date" if granularity == "day" else "substr(usage_date, 1, 7)"
        with self.connect() as connection:
            connection.execute("BEGIN")
            summary = dict(connection.execute(
                f"SELECT {aggregate} FROM llm_usage_calls WHERE {where}", parameters,
            ).fetchone())
            grouped = {
                row["period"]: dict(row) for row in connection.execute(
                    f"SELECT {bucket} AS period, {aggregate} FROM llm_usage_calls "
                    f"WHERE {where} GROUP BY {bucket} ORDER BY period", parameters,
                ).fetchall()
            }
            breakdown = [dict(row) for row in connection.execute(
                f"SELECT scene, model, {aggregate} FROM llm_usage_calls WHERE {where} "
                "GROUP BY scene, model ORDER BY SUM(total_tokens) DESC, scene, model", parameters,
            ).fetchall()]
            total = summary["call_count"]
            page = min(page, max(1, (total + page_size - 1) // page_size))
            calls = [dict(row) for row in connection.execute(
                f"SELECT * FROM llm_usage_calls WHERE {where} "
                "ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
                (*parameters, page_size, (page - 1) * page_size),
            ).fetchall()]
            lifetime = dict(connection.execute("SELECT call_count, metered_count, total_tokens FROM llm_usage_totals WHERE id = 1").fetchone())
            detailed = connection.execute(
                "SELECT COUNT(*), COUNT(total_tokens), COALESCE(SUM(total_tokens), 0) FROM llm_usage_calls"
            ).fetchone()
            legacy = {name: lifetime[name] - detailed[index]
                      for index, name in enumerate(("call_count", "metered_count", "total_tokens"))}
            started_at = connection.execute("SELECT started_at FROM llm_usage_tracking WHERE id = 1").fetchone()[0]
        empty = dict.fromkeys(summary, 0)
        empty["average_duration_ms"] = None
        series = []
        current = start_date if granularity == "day" else start_date.replace(day=1)
        local_started_at = datetime.fromisoformat(started_at).astimezone(ZoneInfo(timezone_name))
        coverage_start = local_started_at.date()
        coverage_period = coverage_start.isoformat() if granularity == "day" else coverage_start.strftime("%Y-%m")
        while current <= end_date:
            period = current.isoformat() if granularity == "day" else current.strftime("%Y-%m")
            before_tracking = period < coverage_period and period not in grouped
            values = dict.fromkeys(summary, None) if before_tracking else empty
            series.append({**grouped.get(period, {"period": period, **values}), "before_tracking": before_tracking})
            current = (current + timedelta(days=1) if granularity == "day"
                       else (current.replace(day=28) + timedelta(days=4)).replace(day=1))
        return {"summary": summary, "series": series, "breakdown": breakdown,
                "calls": {"items": calls, "total": total, "page": page, "page_size": page_size},
                "lifetime": lifetime, "legacy": legacy,
                "tracking_started_at": local_started_at.isoformat(timespec="seconds")}
