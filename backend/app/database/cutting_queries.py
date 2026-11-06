"""Read-only cutting report options and latest production date."""
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.database.sqlserver import SqlServerClient
from app.models.domain import ReportFilter


QUERY_DIR = Path(__file__).with_name('queries')


class CuttingQueryRepository:
    def __init__(self, client: SqlServerClient | None, machine_no: str) -> None:
        self.client = client
        self.machine_no = machine_no

    def list_projects(self, filters: ReportFilter) -> list[dict[str, Any]]:
        return self.client.fetch_all(
            (QUERY_DIR / 'list_projects.sql').read_text(encoding='utf-8'),
            (self.machine_no, filters.start_date, filters.end_exclusive),
        )

    def list_ships(self, filters: ReportFilter) -> list[dict[str, Any]]:
        return self.client.fetch_all(
            (QUERY_DIR / 'list_ships.sql').read_text(encoding='utf-8'),
            (self.machine_no, filters.start_date, filters.end_exclusive, filters.project_code, filters.project_code),
        )

    def latest_production_date(self, on_or_before: date) -> date | None:
        rows = self.client.fetch_all(
            (QUERY_DIR / 'latest_production_date.sql').read_text(encoding='utf-8'),
            (self.machine_no, on_or_before),
        )
        if not rows or rows[0]['latest_date'] is None:
            return None
        value = rows[0]['latest_date']
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])
