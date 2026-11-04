"""One process-owned PostgreSQL state pool and explicit repository transactions."""
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterable
from typing import Iterator

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import Settings


class ExecutionLeaseLost(RuntimeError):
    pass


_execution: ContextVar[tuple | None] = ContextVar('postgres_execution_lease', default=None)


@contextmanager
def execution_scope(database: 'PostgresDatabase', name: str, owner: str):
    token = _execution.set((database.identity, name, owner))
    try:
        yield
    finally:
        _execution.reset(token)


def execution_owner() -> str | None:
    current = _execution.get()
    return current[2] if current else None


def execution_lease_name() -> str | None:
    current = _execution.get()
    return current[1] if current else None


def require_execution_lease(connection: Connection, database: 'PostgresDatabase') -> None:
    current = _execution.get()
    if current is None or current[0] != database.identity:
        return
    # Share-lock first and retain it through commit: takeover/renewal cannot race
    # protected writes. Check wall time again before commit, never transaction now().
    row = connection.execute('''SELECT owner FROM process_leases
        WHERE name=%s AND owner=%s AND expires_at>EXTRACT(EPOCH FROM clock_timestamp())
        FOR SHARE''', (current[1], current[2])).fetchone()
    if row is None:
        raise ExecutionLeaseLost('后台执行租约已失效，拒绝旧执行者读取或提交')


def require_role_recovery(connection: Connection, role: str) -> None:
    current = _execution.get()
    permitted = current is not None and (current[1].startswith('worker:') if role == 'worker' else current[1] == role)
    if not permitted or connection.execute('''
        SELECT owner FROM process_leases WHERE name=%s AND owner=%s
        AND expires_at>EXTRACT(EPOCH FROM clock_timestamp()) FOR SHARE''', (current[1], current[2])).fetchone() is None:
        raise ExecutionLeaseLost(f'只有持有有效 {role} 租约的进程可以恢复所属运行态')


def recovery_owners(connection: Connection, owners: Iterable[str | None]) -> set[str | None]:
    """Do not recover across a renewal/takeover that has not committed yet."""
    owners = set(owners)
    locked = {row['owner'] for row in connection.execute("""SELECT owner FROM process_leases
        WHERE owner=ANY(%s) ORDER BY name FOR SHARE SKIP LOCKED""", (list(owners),))}
    present = {row['owner'] for row in connection.execute('SELECT owner FROM process_leases WHERE owner=ANY(%s)', (list(owners),))}
    return owners - (present - locked)


def lock_key(connection: Connection, key: str) -> None:
    """Serialize one logical identity, including the case where its row is absent."""
    connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (key,))


class PostgresDatabase:
    def __init__(self, settings: Settings):
        self.identity = (settings.postgres_host, settings.postgres_port, settings.postgres_database)
        self.timeout = settings.postgres_connect_timeout_seconds
        self.pool = ConnectionPool(
            kwargs=dict(host=settings.postgres_host, port=settings.postgres_port,
                        dbname=settings.postgres_database, user=settings.postgres_user,
                        password=settings.postgres_password, connect_timeout=self.timeout,
                        options='-c search_path=app_state,pg_catalog -c timezone=UTC', row_factory=dict_row),
            min_size=settings.postgres_pool_min_size, max_size=settings.postgres_pool_max_size,
            max_waiting=32, timeout=self.timeout, reconnect_timeout=self.timeout,
            check=ConnectionPool.check_connection, open=False,
        )

    def __enter__(self):
        self.pool.open(wait=True, timeout=self.timeout)
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def connect(self, *, repeatable_read: bool = False) -> Iterator[Connection]:
        with self.pool.connection(timeout=self.timeout) as connection, connection.transaction():
            if repeatable_read:
                connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            require_execution_lease(connection, self)
            yield connection
            require_execution_lease(connection, self)

    def check_runtime(self) -> None:
        """Runtime verifies the prepared schema and role; it never performs DDL."""
        from app.database.postgres_migrations import validate_schema
        with self.connect() as connection:
            role = connection.execute('''SELECT current_user AS name, rolsuper, rolcreatedb, rolcreaterole,
                has_schema_privilege(current_user, 'app_state', 'CREATE') AS can_create
                FROM pg_roles WHERE rolname=current_user''').fetchone()
            if role['name'] != 'cutting_app' or any(role[k] for k in ('rolsuper', 'rolcreatedb', 'rolcreaterole', 'can_create')):
                raise RuntimeError('Web/Worker必须使用无DDL权限的cutting_app状态库账号')
            validate_schema(connection)

    def healthy(self) -> bool:
        with self.connect() as connection:
            return connection.execute('SELECT 1 AS ok').fetchone()['ok'] == 1

    def close(self):
        self.pool.close()


class PostgresRepository:
    def __init__(self, database: PostgresDatabase, *, connection: Connection | None = None) -> None:
        self.database = database
        self._connection = connection

    @contextmanager
    def connect(self, *, repeatable_read: bool = False) -> Iterator[Connection]:
        if self._connection is not None:
            yield self._connection
        else:
            with self.database.connect(repeatable_read=repeatable_read) as connection:
                yield connection
