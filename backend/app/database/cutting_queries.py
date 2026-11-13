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

    def task_completion_dates(self, task_ids: list[str]) -> dict[str, date]:
        dates: dict[str, date] = {}
        for offset in range(0, len(task_ids), 500):
            batch = task_ids[offset:offset + 500]
            rows = self.client.fetch_all(f'''SELECT CONVERT(varchar(64), TASK_ID) AS task_id,
                TRY_CONVERT(date, NULLIF(LTRIM(RTRIM(CONVERT(varchar(64), END_TIME, 126))), '')) AS production_date FROM dbo.task
                WHERE UPPER(LTRIM(RTRIM(MACHINE_NO)))=UPPER(?) AND STATUS=2
                  AND COALESCE(WORK_TIME,0)>=0 AND TASK_ID IN ({','.join('?' for _ in batch)})''',
                (self.machine_no, *batch))
            for row in rows:
                value = row['production_date']
                if value is not None:
                    value = value.date() if isinstance(value, datetime) else date.fromisoformat(str(value)[:10])
                    task_id = str(row['task_id'])
                    if task_id in dates and dates[task_id] != value:
                        raise ValueError('同一任务存在冲突结束日期，拒绝校正')
                    dates[task_id] = value
        return dates
