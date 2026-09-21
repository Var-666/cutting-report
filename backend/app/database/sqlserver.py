from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from app.config import Settings
from app.services.request_cancellation import cancellable_cursor


class SqlServerConfigurationError(RuntimeError):
    pass


class SqlServerClient:
    SOURCE_LABELS = {
        "production": ("SQLSERVER", "生产数据库"),
        "worker": ("WORKER_SQLSERVER", "工人数据库"),
        "file": ("FILE_SQLSERVER", "文件数据库"),
    }

    def __init__(self, settings: Settings, *, source: str = "production") -> None:
        if source not in self.SOURCE_LABELS:
            raise ValueError(f"不支持的数据源：{source}")
        self.settings = settings
        self.source = source

    def _connection_string(self) -> str:
        config = self.settings.sqlserver_connection(self.source)
        env_prefix, source_label = self.SOURCE_LABELS[self.source]
        required = {
            f"{env_prefix}_HOST": config["host"],
            f"{env_prefix}_DATABASE": config["database"],
            f"{env_prefix}_USERNAME": config["username"],
            f"{env_prefix}_PASSWORD": config["password"],
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise SqlServerConfigurationError(
                f"{source_label}配置不完整：{', '.join(missing)}"
            )
        return ";".join(
            [
                f"DRIVER={{{config['driver']}}}",
                f"SERVER={config['host']},{config['port']}",
                f"DATABASE={config['database']}",
                f"UID={config['username']}",
                f"PWD={config['password']}",
                f"Encrypt={config['encrypt']}",
                f"TrustServerCertificate={config['trust_server_certificate']}",
                f"Connection Timeout={config['connect_timeout']}",
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
            connection.timeout = int(
                self.settings.sqlserver_connection(self.source)["query_timeout"]
            )
            yield connection
        finally:
            connection.close()

    def fetch_all(self, sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self.connect() as connection:
            with cancellable_cursor(connection) as cursor:
                cursor.execute(sql, parameters)
                columns = [item[0] for item in cursor.description]
                return [
                    dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
                ]

    def fetch_result_sets(
        self, sql: str, parameters: tuple[Any, ...]
    ) -> list[list[dict[str, Any]]]:
        """Execute one SQL batch and return every tabular result set."""
        with self.connect() as connection:
            with cancellable_cursor(connection) as cursor:
                cursor.execute(sql, parameters)
                result_sets: list[list[dict[str, Any]]] = []
                while True:
                    if cursor.description is not None:
                        columns = [item[0] for item in cursor.description]
                        result_sets.append(
                            [
                                dict(zip(columns, row, strict=True))
                                for row in cursor.fetchall()
                            ]
                        )
                    if not cursor.nextset():
                        break
                return result_sets
