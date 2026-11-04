"""P4 state-store tooling. Not wired into the SQLite application runtime."""
from contextlib import contextmanager
from typing import Iterator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import Settings


class PostgresDatabase:
    def __init__(self, settings: Settings):
        self.timeout = settings.postgres_connect_timeout_seconds
        self.pool = ConnectionPool(
            kwargs=dict(host=settings.postgres_host, port=settings.postgres_port,
                        dbname=settings.postgres_database, user=settings.postgres_user,
                        password=settings.postgres_password, connect_timeout=self.timeout,
                        options='-c search_path=app_state,pg_catalog', row_factory=dict_row),
            min_size=settings.postgres_pool_min_size, max_size=settings.postgres_pool_max_size,
            max_waiting=32, timeout=self.timeout, reconnect_timeout=self.timeout,
            check=ConnectionPool.check_connection, open=False,
        )

    def __enter__(self):
        self.pool.open()
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        """Commit on success, rollback on exception, always return the connection."""
        with self.pool.connection(timeout=self.timeout) as connection:
            yield connection

    def healthy(self) -> bool:
        with self.connect() as connection:
            return connection.execute('SELECT 1 AS ok').fetchone()['ok'] == 1

    def close(self):
        self.pool.close()
