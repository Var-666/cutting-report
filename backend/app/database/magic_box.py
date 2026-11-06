"""Read-only magic-box connection settings from production SYS_CONFIG."""
from typing import Any

from app.core.sql_capacity import sql_identity
from app.database.sqlserver import SqlServerClient


CONFIG_KEYS = (
    "CUTTER_GATEWAY",
    "CUTTER_TOKEN",
    "CUTTER_SECRET",
    "CUTTER01_IP",
    "CUTTER02_IP",
)


class MagicBoxConfigRepository:
    def __init__(self, client: SqlServerClient | None) -> None:
        self.client = client

    def configuration_rows(self) -> list[dict[str, Any]]:
        with sql_identity(None):
            return self.client.fetch_all(
                """
                SELECT CONFIG_NAME, CONFIG_VALUE
                FROM dbo.SYS_CONFIG
                WHERE CONFIG_NAME IN (?, ?, ?, ?, ?)
                """,
                CONFIG_KEYS,
            )
