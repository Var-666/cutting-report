from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic
from typing import Iterator


class ReportOperationTimeout(TimeoutError):
    """A background report exceeded its complete query/render budget."""


_deadline: ContextVar[float | None] = ContextVar("report_operation_deadline", default=None)


@contextmanager
def operation_deadline(timeout_seconds: float) -> Iterator[None]:
    token = _deadline.set(monotonic() + timeout_seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


@contextmanager
def report_deadline(timeout_seconds: float) -> Iterator[None]:
    with operation_deadline(timeout_seconds):
        yield


def remaining_seconds() -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return None
    return deadline - monotonic()


def ensure_time_remaining() -> None:
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        raise ReportOperationTimeout("报表生成超过总时限，请稍后重试。")
