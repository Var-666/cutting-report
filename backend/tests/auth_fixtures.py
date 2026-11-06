"""Shared HR authentication rows, configuration and recording SQL client."""
from dataclasses import replace

from app.config import settings


class FakeSqlServerClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = []

    def fetch_all(self, sql, parameters):
        self.calls.append((sql, parameters))
        if self.error:
            raise self.error
        return self.rows


def enabled_settings(**changes):
    return replace(settings, worker_database_auth_enabled=True, **changes)


def valid_row(**changes):
    row = {
        "external_id": 9123,
        "display_name": "张三",
        "group_no": "G0021",
        "company_no": "C0007",
        "available_value": 1,
        "password_matches": 1,
    }
    row.update(changes)
    return row
