"""Bound blocking HTTP work without borrowing FastAPI's default thread tokens."""
from functools import wraps
from inspect import signature
from collections import deque
import logging
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Any, Callable

from anyio import CapacityLimiter, fail_after, sleep, to_thread
from fastapi import HTTPException, Request

from app.services.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations


logger = logging.getLogger(__name__)


class RequestWorkload:
    def __init__(self, limit: int, label: str, *, max_waiters: int = 0, queue_timeout_seconds: float = 5) -> None:
        if limit < 1 or max_waiters < 0 or queue_timeout_seconds <= 0:
            raise ValueError('Invalid workload capacity or queue timeout')
        self._slots = BoundedSemaphore(limit)
        self._threads = CapacityLimiter(limit)
        self._queue = deque()
        self._queue_lock = Lock()
        self.max_waiters = max_waiters
        self.queue_timeout_seconds = queue_timeout_seconds
        self.label = label

    def _busy(self, reason: str) -> HTTPException:
        if self.max_waiters:
            logger.warning('workload rejected request_id=%s pool=%s reason=%s', current_request_id.get(), self.label, reason)
        message = {'queue_full': '等待队列已满', 'queue_timeout': '排队等待超时'}.get(reason, '请求繁忙')
        headers = {'Retry-After': '1'}
        if self.max_waiters:
            headers['X-Workload-Error'] = reason
        return HTTPException(status_code=503, detail=f'{self.label}{message}，请稍后重试。', headers=headers)

    async def _acquire(self, request: Request | None) -> None:
        if not self.max_waiters:
            if not self._slots.acquire(blocking=False):
                raise self._busy('busy')
            return
        ticket = object()
        with self._queue_lock:
            if not self._queue and self._slots.acquire(blocking=False):
                return
            if len(self._queue) >= self.max_waiters:
                raise self._busy('queue_full')
            self._queue.append(ticket)
        try:
            with fail_after(self.queue_timeout_seconds):
                while True:
                    if sql_request_cancellations.is_cancelled(current_request_id.get()) or (
                        request is not None and await request.is_disconnected()
                    ):
                        raise SqlRequestCancelled('焊接查询等待已取消')
                    with self._queue_lock:
                        if self._queue[0] is ticket and self._slots.acquire(blocking=False):
                            self._queue.popleft()
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
        request = _request or next((value for value in (*args, *kwargs.values()) if isinstance(value, Request)), None)
        queued_at = monotonic()
        await self._acquire(request)
        pending = True
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
                self._slots.release()
                if self.max_waiters:
                    logger.info('workload finished request_id=%s operation=%s elapsed_ms=%d', current_request_id.get(), getattr(operation, '__name__', type(operation).__name__), round((monotonic() - queued_at) * 1000))

        try:
            return await to_thread.run_sync(execute, limiter=self._threads)
        finally:
            with dispatch_lock:
                if pending:
                    pending = False
                    self._slots.release()

    def wrap(self, endpoint: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(endpoint)
        async def run(*args: Any, **kwargs: Any) -> Any:
            return await self.run_sync(endpoint, *args, **kwargs)

        # Resolve postponed annotations in the endpoint's module, not this module.
        run.__signature__ = signature(endpoint, eval_str=True)
        return run

    def route(self, register: Callable[..., Any]) -> Callable[..., Any]:
        def decorate(endpoint: Callable[..., Any]) -> Callable[..., Any]:
            register(self.wrap(endpoint))
            return endpoint
        return decorate


analysis_requests = RequestWorkload(10, "智能分析")
pdf_requests = RequestWorkload(2, "PDF 导出")
query_requests = RequestWorkload(8, "报表查询")
authentication_requests = RequestWorkload(8, "身份验证")
