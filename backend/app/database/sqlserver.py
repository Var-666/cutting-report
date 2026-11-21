from __future__ import annotations

from contextlib import contextmanager
from math import ceil
from sys import modules
from typing import Any, Iterator

import logging

from app.core.operations_logging import event
from app.models.database_sources import DatabaseSourceConfig
from app.core.request_cancellation import SqlRequestCancelled, cancellable_cursor
from app.core.report_deadline import ReportOperationTimeout, ensure_time_remaining, remaining_seconds
from app.core.sql_capacity import SqlCapacity


logger = logging.getLogger(__name__)


class SqlServerConfigurationError(RuntimeError):
    pass


def sqlserver_driver_errors() -> tuple[type[Exception], ...]:
    # ODBC is optional, and a driver exception can only come from an already loaded driver.
    driver = modules.get('pyodbc')
    return (driver.Error,) if driver is not None else ()


class SqlServerClient:
    def __init__(self, config: DatabaseSourceConfig, *, capacity: SqlCapacity, probe: bool = False) -> None:
        if config.engine != 'sqlserver':
            raise ValueError('数据源不是 SQL Server')
        self.settings = config
        self.source = config.id
        self.capacity = capacity
        self._probe = probe

    def connection_config(self) -> dict:
        if not self.settings.enabled:
            raise SqlServerConfigurationError('数据源尚未启用')
        return self.settings.sqlserver_connection()

    def _connection_string(self) -> str:
        config = self.connection_config()
        ensure_time_remaining()
        remaining = remaining_seconds()
        connect_timeout = config["connect_timeout"]
        if remaining is not None:
            connect_timeout = max(1, min(connect_timeout, ceil(remaining)))
        env_prefix, source_label = ('SOURCE', '数据库')
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
                "DRIVER={" + str(config['driver']).replace('}', '}}') + "}",
                f"SERVER={self._odbc_value(str(config['host']) + ',' + str(config['port']))}",
                f"DATABASE={self._odbc_value(config['database'])}",
                f"UID={self._odbc_value(config['username'])}",
                f"PWD={self._odbc_value(config['password'])}",
                f"Encrypt={config['encrypt']}",
                f"TrustServerCertificate={config['trust_server_certificate']}",
                f"Connection Timeout={connect_timeout}",
            ]
        )

    @staticmethod
    def _odbc_value(value: str) -> str:
        # Preserve ordinary legacy strings and escape delimiters in credentials.
        return '{' + value.replace('}', '}}') + '}' if any(char in value for char in ';{}') else value

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.settings.legacy_tls:
            raise SqlServerConfigurationError('旧版 TLS 兼容仅支持隔离进程中的批量只读查询')
        try:
            import pyodbc
        except ImportError as exc:
            raise SqlServerConfigurationError(
                "未安装 pyodbc，请用 uv 为应用环境同步 sqlserver 可选依赖"
            ) from exc
        options = {'readonly': True}
        self.connection_config()
        with self.capacity.slot(self.settings, probe=self._probe):
            try:
                connection = pyodbc.connect(self._connection_string(), **options)
            except pyodbc.Error:
                event(logger, logging.ERROR, 'SQL Server 连接失败', error_code='SQLSERVER_CONNECT_FAILED',
                      source_id=self.source, incident=not self._probe, exc_info=True)
                raise
            try:
                query_timeout = self.connection_config()["query_timeout"]
                remaining = remaining_seconds()
                if remaining is not None:
                    query_timeout = max(1, min(query_timeout, ceil(remaining)))
                connection.timeout = int(query_timeout)
                yield connection
            except pyodbc.Error:
                event(logger, logging.ERROR, 'SQL Server 查询失败', error_code='SQLSERVER_QUERY_FAILED',
                      source_id=self.source, incident=not self._probe, exc_info=True)
                raise
            finally:
                connection.close()

    def fetch_all(self, sql: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        if self.settings.legacy_tls:
            from app.database.sqlserver_legacy import fetch_all
            self.connection_config()  # Reject disabled sources before starting a child.
            with self.capacity.slot(self.settings, probe=self._probe):
                try:
                    return fetch_all(self.settings, sql, parameters)
                except (SqlRequestCancelled, ReportOperationTimeout):
                    raise
                except Exception:
                    event(logger, logging.ERROR, 'SQL Server 兼容连接或查询失败', error_code='SQLSERVER_LEGACY_FAILED',
                          source_id=self.source, incident=not self._probe, exc_info=True)
                    raise
        with self.connect() as connection, cancellable_cursor(connection) as cursor:
            cursor.execute(sql, parameters)
            columns = [item[0] for item in cursor.description]
            return [
                dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
            ]

    def iter_rows(self, sql: str, parameters: tuple[Any, ...]) -> Iterator[dict[str, Any]]:
        """Stream one result set with bounded batch memory; caller must close early exits."""
        with self.connect() as connection, cancellable_cursor(connection) as cursor:
            cursor.execute(sql, parameters)
            columns = [item[0] for item in cursor.description]
            while batch := cursor.fetchmany(500):
                for row in batch:
                    yield dict(zip(columns, row, strict=True))

    def fetch_result_sets(
        self, sql: str, parameters: tuple[Any, ...]
    ) -> list[list[dict[str, Any]]]:
        """Execute one SQL batch and return every tabular result set."""
        with self.connect() as connection, cancellable_cursor(connection) as cursor:
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
