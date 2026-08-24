from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.config import Settings


class SqlServerConfigurationError(RuntimeError):
    pass


class SqlServerClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _connection_string(self) -> str:
        required = {
            "SQLSERVER_HOST": self.settings.sqlserver_host,
            "SQLSERVER_DATABASE": self.settings.sqlserver_database,
            "SQLSERVER_USERNAME": self.settings.sqlserver_username,
            "SQLSERVER_PASSWORD": self.settings.sqlserver_password,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise SqlServerConfigurationError(
                f"SQL Server 配置不完整：{', '.join(missing)}"
            )
        return ";".join(
            [
                f"DRIVER={{{self.settings.sqlserver_driver}}}",
                f"SERVER={self.settings.sqlserver_host},{self.settings.sqlserver_port}",
                f"DATABASE={self.settings.sqlserver_database}",
                f"UID={self.settings.sqlserver_username}",
                f"PWD={self.settings.sqlserver_password}",
                f"Encrypt={self.settings.sqlserver_encrypt}",
                f"TrustServerCertificate={self.settings.sqlserver_trust_server_certificate}",
                f"Connection Timeout={self.settings.sqlserver_connect_timeout}",
            ]
        )

    @contextmanager
    def connect(self) -> Iterator[Any]:
        try:
            import pyodbc
        except ImportError as exc:
            raise SqlServerConfigurationError(
                "未安装 pyodbc，请安装 backend/requirements-sqlserver.txt"
            ) from exc
        connection = pyodbc.connect(self._connection_string())
        try:
            connection.timeout = self.settings.sqlserver_query_timeout
            yield connection
        finally:
            connection.close()

    def fetch_all(self, sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self.connect() as connection:
            cursor = connection.cursor()
            cursor.execute(sql, parameters)
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

