"""Bound blocking HTTP work without borrowing FastAPI's default thread tokens."""
from functools import wraps
from inspect import signature
from collections import deque
import logging
from threading import BoundedSemaphore, Event, Lock
from time import monotonic
from typing import Any, Callable

from anyio import CapacityLimiter, create_task_group, fail_after, get_cancelled_exc_class, sleep, sleep_forever, to_thread
from anyio.lowlevel import checkpoint_if_cancelled
from fastapi import HTTPException, Request

from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations
from app.core.sql_capacity import current_sql_identity, default_sql_capacity, sql_queue_budget
from app.core.report_deadline import operation_deadline, remaining_seconds
from app.config import settings


logger = logging.getLogger(__name__)


class RequestWorkload:
    def __init__(self, limit: int, label: str, *, max_waiters: int = 0, queue_timeout_seconds: float = 5, partitioned: bool = False) -> None:
        if limit < 1 or max_waiters < 0 or queue_timeout_seconds <= 0:
            raise ValueError('Invalid workload capacity or queue timeout')
        self._slots = BoundedSemaphore(limit)
        self._threads = CapacityLimiter(limit)
        self._queue = deque()
        self._queue_lock = Lock()
        self.max_waiters = max_waiters
        self.queue_timeout_seconds = queue_timeout_seconds
        self.label = label
        self.limit = limit
        self.partitioned = partitioned
        self._active_keys = {}
        self._executing = 0
        self._timeouts = 0
        self._rejected = 0

    def _busy(self, reason: str) -> HTTPException:
        with self._queue_lock:
            if reason == 'queue_timeout': self._timeouts += 1
            else: self._rejected += 1
        if self.max_waiters:
            logger.warning('workload rejected request_id=%s pool=%s reason=%s', current_request_id.get(), self.label, reason)
        message = {'queue_full': '等待队列已满', 'queue_timeout': '排队等待超时'}.get(reason, '请求繁忙')
        headers = {'Retry-After': '1'}
        if self.max_waiters:
            headers['X-Workload-Error'] = reason
        return HTTPException(status_code=503, detail=f'{self.label}{message}，请稍后重试。', headers=headers)

    def _keys(self, request):
        identity = current_sql_identity.get()
        if not self.partitioned or not (identity.line_id or identity.source_key):
            return ()
        context = getattr(request.state, 'database_context', None) if request is not None else None
        sources = getattr(context, 'database_sources', None)
        capacity = sources.capacity if sources is not None else default_sql_capacity()
        keys = []
        if identity.line_id: keys.append((f'line:{identity.line_id}', capacity.line_limit))
        if identity.source_key: keys.append((f'source:{identity.source_key}', capacity.source_limit(identity.source_key)))
        return tuple(keys)

    def _take(self, keys):
        if any(self._active_keys.get(key, 0) >= limit for key, limit in keys):
            return False
        if not self._slots.acquire(blocking=False):
            return False
        for key, _ in keys:
            self._active_keys[key] = self._active_keys.get(key, 0) + 1
        self._executing += 1
        return True

    def _release(self, keys):
        with self._queue_lock:
            for key, _ in keys:
                self._active_keys[key] -= 1
                if not self._active_keys[key]: del self._active_keys[key]
            self._executing -= 1
            self._slots.release()

    def status(self):
        with self._queue_lock:
            return dict(resource_type='http', resource=self.label, executing=self._executing,
                execution_capacity=self.limit, waiting=len(self._queue), waiting_capacity=self.max_waiters,
                timeouts=self._timeouts, rejected=self._rejected)

    async def _acquire(self, request: Request | None, keys=()) -> None:
        if not self.max_waiters:
            with self._queue_lock:
                acquired = self._take(keys)
            if acquired: return
            raise self._busy('busy')
        ticket = (object(), keys[0][0] if keys else None)
        with self._queue_lock:
            if not any(item[1] == ticket[1] for item in self._queue) and self._take(keys):
                return
            full = len(self._queue) >= self.max_waiters
            if not full: self._queue.append(ticket)
        if full:
            raise self._busy('queue_full')
        try:
            remaining = remaining_seconds()
            timeout = self.queue_timeout_seconds if remaining is None else min(self.queue_timeout_seconds, max(0, remaining))
            with fail_after(timeout):
                while True:
                    if sql_request_cancellations.is_cancelled(current_request_id.get()) or (
                        request is not None and await request.is_disconnected()
                    ):
                        raise SqlRequestCancelled('请求等待已取消')
                    with self._queue_lock:
                        earlier = list(self._queue)[:self._queue.index(ticket)]
                        if not any(item[1] == ticket[1] for item in earlier) and self._take(keys):
                            self._queue.remove(ticket)
                            return
                    # FIFO polling lets the worker release its native semaphore safely
                    # even when the HTTP task or its event loop has already gone away.
                    await sleep(0.025)
        except TimeoutError:
            raise self._busy('queue_timeout') from None
        finally:
            with self._queue_lock:
                if ticket in self._queue:
                    self._queue.remove(ticket)

    async def run_sync(self, operation: Callable[..., Any], *args: Any, _request: Request | None = None, **kwargs: Any) -> Any:
        with operation_deadline(settings.report_operation_timeout_seconds), sql_queue_budget(self.queue_timeout_seconds):
            return await self._run_sync(operation, *args, _request=_request, **kwargs)

    async def _run_sync(self, operation: Callable[..., Any], *args: Any, _request: Request | None = None, **kwargs: Any) -> Any:
        request = _request or next((value for value in (*args, *kwargs.values()) if isinstance(value, Request)), None)
        queued_at = monotonic()
        keys = self._keys(request)
        await self._acquire(request, keys)
        pending = True
        finished = Event()
        dispatch_lock = Lock()
        wait_ms = round((monotonic() - queued_at) * 1000)
        request_id = getattr(request.state, 'request_id', None) if request is not None else None

        def execute() -> Any:
            nonlocal pending
            with dispatch_lock:
                if not pending:
                    return None
                pending = False
            try:
                if self.max_waiters and sql_request_cancellations.is_cancelled(current_request_id.get()):
                    raise SqlRequestCancelled('焊接查询已取消')
                if self.max_waiters:
                    logger.info('workload started request_id=%s execution_id=%s operation=%s wait_ms=%d', request_id, current_request_id.get(), getattr(operation, '__name__', type(operation).__name__), wait_ms)
                return operation(*args, **kwargs)
            finally:
                # Native asyncio cancellation can abandon the await, not the thread.
                self._release(keys)
                finished.set()
                if self.max_waiters:
                    logger.info('workload finished request_id=%s operation=%s elapsed_ms=%d', current_request_id.get(), getattr(operation, '__name__', type(operation).__name__), round((monotonic() - queued_at) * 1000))

        try:
            async def watch_disconnect():
                try:
                    if request is None:
                        await sleep_forever()
                    while True:
                        if await request.is_disconnected():
                            sql_request_cancellations.cancel(current_request_id.get())
                            return
                        await sleep(0.05)
                finally:
                    if not finished.is_set():
                        sql_request_cancellations.cancel(current_request_id.get())
            async with create_task_group() as group:
                group.start_soon(watch_disconnect)
                error = None
                try:
                    result = await to_thread.run_sync(execute, limiter=self._threads)
                except Exception as exc:
                    error = exc
                finally:
                    group.cancel_scope.cancel()
            await checkpoint_if_cancelled()
            if error is not None:
                raise error
            return result
        except get_cancelled_exc_class():
            sql_request_cancellations.cancel(current_request_id.get())
            raise
        finally:
            with dispatch_lock:
                if pending:
                    pending = False
                    self._release(keys)

    def wrap(self, endpoint: Callable[..., Any], *, local_when=None) -> Callable[..., Any]:
        @wraps(endpoint)
        async def run(*args: Any, **kwargs: Any) -> Any:
            request = next((value for value in (*args, *kwargs.values()) if isinstance(value, Request)), None)
            if local_when and request is not None and local_when(request):
                return await snapshot_requests.run_sync(endpoint, *args, **kwargs)
            return await self.run_sync(endpoint, *args, **kwargs)

        # Resolve postponed annotations in the endpoint's module, not this module.
        run.__signature__ = signature(endpoint, eval_str=True)
        return run

    def route(self, register: Callable[..., Any], *, local_when=None) -> Callable[..., Any]:
        def decorate(endpoint: Callable[..., Any]) -> Callable[..., Any]:
            register(self.wrap(endpoint, local_when=local_when))
            return endpoint
        return decorate


_queue = dict(max_waiters=settings.http_max_waiters, queue_timeout_seconds=settings.sql_queue_timeout_seconds)
analysis_requests = RequestWorkload(settings.http_analysis_max_concurrent, "智能分析", **_queue)
pdf_requests = RequestWorkload(settings.http_pdf_max_concurrent, "PDF 导出", **_queue)
query_requests = RequestWorkload(settings.http_query_max_concurrent, "报表查询", partitioned=True, **_queue)
authentication_requests = RequestWorkload(settings.http_auth_max_concurrent, "身份验证", **_queue)
snapshot_requests = RequestWorkload(settings.http_snapshot_max_concurrent, "快照读取", **_queue)
