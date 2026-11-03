"""Bound external SQL work across threads and local Web/Worker processes."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
import logging
from threading import Event, Lock
from time import monotonic
from typing import TYPE_CHECKING

from app.core.process_lock import FileLock
from app.core.report_deadline import ReportOperationTimeout, ensure_time_remaining, remaining_seconds
from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations

if TYPE_CHECKING:
    from app.config import Settings
    from app.models.database_sources import DatabaseSourceConfig

logger = logging.getLogger(__name__)


class SqlCapacityError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        message = '数据库资源等待队列已满' if reason == 'queue_full' else '数据库资源排队超时'
        super().__init__(message + '，请稍后重试')


@dataclass(frozen=True)
class SqlIdentity:
    line_id: str | None = None
    source_key: str | None = None


current_sql_identity = ContextVar('sql_identity', default=SqlIdentity())
_queue_deadline: ContextVar[float | None] = ContextVar('sql_queue_deadline', default=None)
_inherited_permit = ContextVar('inherited_sql_permit', default=False)
_permit_fds: ContextVar[tuple[int, ...]] = ContextVar('sql_permit_fds', default=())


def sql_permit_fds() -> tuple[int, ...]:
    return _permit_fds.get()


@contextmanager
def sql_identity(line_id: str | None, source_key: str | None = None):
    token = current_sql_identity.set(SqlIdentity(line_id, source_key))
    try:
        yield
    finally:
        current_sql_identity.reset(token)


@contextmanager
def sql_queue_budget(seconds: float):
    old = _queue_deadline.get()
    deadline = monotonic() + seconds
    token = _queue_deadline.set(min(old, deadline) if old is not None else deadline)
    try:
        yield
    finally:
        _queue_deadline.reset(token)


@contextmanager
def inherited_sql_permit():
    """Only the trusted legacy-TLS child may reuse its parent's held SQL permit."""
    token = _inherited_permit.set(True)
    try:
        yield
    finally:
        _inherited_permit.reset(token)


class SqlCapacity:
    def __init__(self, settings: Settings):
        self.directory = settings.data_dir / '.coordination' / 'sql'
        self.global_limit = settings.sql_global_max_concurrent
        self.wait_limit = settings.sql_global_max_waiters
        self.timeout = settings.sql_queue_timeout_seconds
        self.line_limit = min(settings.sql_line_max_concurrent, self.global_limit)
        self.defaults = {'sqlserver': settings.sqlserver_default_max_concurrent, 'mysql': settings.mysql_default_max_concurrent}
        self._limits: dict[str, int] = {}
        self._stats: dict[tuple[str, str], dict] = {}
        self._lock = Lock()

    def _source_limit(self, config: DatabaseSourceConfig) -> int:
        default = self.defaults[config.engine]
        if config.engine == 'mysql':
            default = min(default, config.pool_size)
        return min(config.sql_max_concurrent or default, self.global_limit)

    def register_sources(self, configs) -> None:
        # Capacities stay fixed until both processes restart; connection snapshots remain hot.
        with self._lock:
            for config in configs:
                self._limits.setdefault(config.id, self._source_limit(config))

    def source_limit(self, source_key: str | None) -> int:
        with self._lock:
            return self._limits.get(source_key, min(self.defaults['sqlserver'], self.global_limit))

    def knows_source(self, source_key: str) -> bool:
        with self._lock:
            return source_key in self._limits

    def _resource(self, kind: str, identity: str, limit: int) -> dict:
        key = kind, identity
        return self._stats.setdefault(key, dict(resource_type=kind, resource=identity, executing=0,
            execution_capacity=limit, waiting=0, waiting_capacity=self.wait_limit, timeouts=0, rejected=0))

    def status(self, configs=()) -> dict:
        with self._lock:
            self._resource('global', 'sql', self.global_limit)
            for config in configs:
                stat = self._resource('source', config.id, self._limits[config.id])
                stat['configured_capacity'] = self._source_limit(config)
                stat['restart_required'] = stat['execution_capacity'] != stat['configured_capacity']
            return {'scope': 'process', 'coordination': 'shared_local_directory',
                    'items': [dict(value) for value in self._stats.values()]}

    def _take(self, kind: str, identity: str, limit: int) -> FileLock | None:
        name = sha256(identity.encode()).hexdigest()[:24]
        for index in range(limit):
            slot = FileLock(self.directory / f'{kind}-{name}-{index}.lock')
            if slot.acquire(blocking=False):
                return slot
        return None

    def _try_all(self, resources, pool):
        held = []
        pool_held = False
        try:
            if pool is not None:
                pool_held = pool.acquire(blocking=False)
                if not pool_held:
                    return None
            # All acquisitions are nonblocking; a miss returns every earlier permit.
            for kind, identity, limit in reversed(resources):
                slot = self._take(kind, identity, limit)
                if slot is None:
                    return None
                held.append(slot)
            result = held
            held = []
            pool_held = False
            return result
        finally:
            for slot in reversed(held):
                slot.release()
            if pool_held:
                pool.release()

    @contextmanager
    def slot(self, config: DatabaseSourceConfig, *, pool=None, probe: bool = False):
        if _inherited_permit.get():
            yield
            return
        with self._lock:
            # Unsaved connection probes have a fixed identity, never per-request lock files.
            source = f'probe:{config.engine}' if probe else config.id
            if not probe:
                self._limits.setdefault(source, self._source_limit(config))
            limit = self._limits.get(source, min(self.defaults[config.engine], self.global_limit))
        identity = current_sql_identity.get()
        resources = [('global', 'sql', self.global_limit), ('source', source, limit)]
        if identity.line_id:
            resources.append(('line', identity.line_id, self.line_limit))
        with self._lock:
            stats = [self._resource(*resource) for resource in resources]
        held = None
        ticket = None
        waiting = False
        started = monotonic()
        deadline = min(_queue_deadline.get() or started + self.timeout, started + self.timeout)
        pending = Event()
        class Waiter:
            def cancel(self): pending.set()
        waiter = Waiter()
        request_id = current_request_id.get()
        sql_request_cancellations.register(request_id, waiter)
        try:
            while True:
                ensure_time_remaining()
                if pending.is_set() or sql_request_cancellations.is_cancelled(request_id):
                    raise SqlRequestCancelled('数据库资源等待已取消')
                held = self._try_all(resources, pool)
                if held is not None:
                    break
                if not waiting:
                    ticket = self._take('wait', 'sql', self.wait_limit)
                    if ticket is None:
                        with self._lock:
                            for stat in stats: stat['rejected'] += 1
                        raise SqlCapacityError('queue_full')
                    waiting = True
                    with self._lock:
                        for stat in stats: stat['waiting'] += 1
                budget = deadline - monotonic()
                remaining = remaining_seconds()
                if remaining is not None:
                    budget = min(budget, remaining)
                if budget <= 0:
                    ensure_time_remaining()
                    with self._lock:
                        for stat in stats: stat['timeouts'] += 1
                    raise SqlCapacityError('queue_timeout')
                pending.wait(min(0.05, budget))
            if ticket:
                ticket.release()
                ticket = None
            with self._lock:
                for stat in stats:
                    if waiting: stat['waiting'] -= 1
                    stat['executing'] += 1
            waiting = False
            entered = monotonic()
            logger.info('sql admitted source=%s line=%s wait_ms=%d', source, identity.line_id or '-', round((entered-started)*1000))
            descriptor_token = _permit_fds.set(tuple(slot.handle.fileno() for slot in held))
            try:
                yield
            finally:
                _permit_fds.reset(descriptor_token)
                logger.info('sql finished source=%s line=%s execution_ms=%d', source, identity.line_id or '-', round((monotonic()-entered)*1000))
                with self._lock:
                    for stat in stats: stat['executing'] -= 1
        except ReportOperationTimeout:
            if waiting:
                with self._lock:
                    for stat in stats: stat['timeouts'] += 1
                logger.warning('sql rejected source=%s line=%s reason=operation_timeout wait_ms=%d', source,
                               identity.line_id or '-', round((monotonic()-started)*1000))
            raise
        except SqlCapacityError as exc:
            logger.warning('sql rejected source=%s line=%s reason=%s wait_ms=%d', source,
                           identity.line_id or '-', exc.reason, round((monotonic()-started)*1000))
            raise
        finally:
            sql_request_cancellations.unregister(request_id, waiter)
            if ticket:
                ticket.release()
            if waiting:
                with self._lock:
                    for stat in stats: stat['waiting'] -= 1
            if held is not None:
                for slot in reversed(held): slot.release()
                if pool is not None: pool.release()


@lru_cache(maxsize=1)
def default_sql_capacity() -> SqlCapacity:
    from app.config import settings
    return SqlCapacity(settings)
