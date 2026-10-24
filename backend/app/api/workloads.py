"""Bound blocking HTTP work without borrowing FastAPI's default thread tokens."""
from functools import partial, wraps
from inspect import signature
from threading import BoundedSemaphore
from typing import Any, Callable

from anyio import CapacityLimiter, to_thread
from fastapi import HTTPException


class RequestWorkload:
    def __init__(self, limit: int, label: str) -> None:
        self._slots = BoundedSemaphore(limit)
        self._threads = CapacityLimiter(limit)
        self.label = label

    async def run_sync(self, operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        # Reject before dispatch: waiting for capacity must not create a queue of threads.
        if not self._slots.acquire(blocking=False):
            raise HTTPException(
                status_code=503,
                detail=f"{self.label}请求繁忙，请稍后重试。",
                headers={"Retry-After": "1"},
            )
        try:
            return await to_thread.run_sync(
                partial(operation, *args, **kwargs), limiter=self._threads,
            )
        finally:
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
