"""Timing and shared waiting budget for one report analysis operation."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

from app.config import Settings
from app.services.report_deadline import ensure_time_remaining, operation_deadline, remaining_seconds
from app.services.request_cancellation import current_request_id


logger = logging.getLogger(__name__)


@dataclass
class AnalysisExecution:
    started: float
    queue_budget: float
    stages: dict[str, float] = field(default_factory=dict)
    cache: str = "miss"
    promoted: bool = False
    input_chars: int = 0
    analysis_id: str = field(default_factory=lambda: uuid4().hex)
    model_attempts: int = 0


_execution: ContextVar[AnalysisExecution | None] = ContextVar("analysis_execution", default=None)


@contextmanager
def analysis_operation(settings: Settings, report_type: str, report_date: str,
                       *, background: bool = False, request_id: str | None = None):
    # The API includes data preparation; service callers (refresh/export) start
    # here themselves. Nested scopes must not reset the clock or export deadline.
    if _execution.get() is not None:
        yield
        return
    execution = AnalysisExecution(monotonic(), settings.llm_queue_timeout_seconds)
    token = _execution.set(execution)
    budget = settings.llm_queue_timeout_seconds + settings.llm_timeout
    outer_remaining = remaining_seconds()
    if outer_remaining is not None:
        budget = min(budget, outer_remaining)
    outcome = "success"
    try:
        with operation_deadline(budget):
            ensure_time_remaining()
            yield
            ensure_time_remaining()
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        total = monotonic() - execution.started
        stages = execution.stages
        prepare = stages.get("prepare", total - sum(stages.values()))
        logger.info(
            "report analysis timing: request_id=%s device=%s report_type=%s report_date=%s "
            "background=%s outcome=%s cache=%s total_ms=%d prepare_ms=%d cache_ms=%d "
            "same_version_wait_ms=%d capacity_wait_ms=%d model_ms=%d "
            "finalize_ms=%d promoted=%s input_chars=%d analysis_id=%s model_attempts=%d retry_wait_ms=%d",
            request_id or current_request_id.get() or "-", settings.device_id, report_type,
            report_date, background, outcome, execution.cache, round(total * 1000),
            round(prepare * 1000), round(stages.get("cache", 0) * 1000),
            round(stages.get("same_version_wait", 0) * 1000),
            round(stages.get("capacity_wait", 0) * 1000), round(stages.get("model", 0) * 1000),
            round(max(0, total - prepare - sum(v for k, v in stages.items() if k != "prepare")) * 1000),
            execution.promoted, execution.input_chars, execution.analysis_id, execution.model_attempts,
            round(stages.get("retry_wait", 0) * 1000),
        )
        _execution.reset(token)


@contextmanager
def analysis_stage(name: str):
    started = monotonic()
    try:
        yield
    finally:
        execution = _execution.get()
        if execution is not None:
            execution.stages[name] = execution.stages.get(name, 0) + monotonic() - started


def analysis_prepared() -> None:
    execution = _execution.get()
    if execution is not None:
        execution.stages.setdefault("prepare", monotonic() - execution.started)


def analysis_observation(*, cache: str | None = None, promoted: bool = False,
                         input_chars: int = 0, model_attempt: bool = False) -> None:
    execution = _execution.get()
    if execution is not None:
        if cache is not None:
            execution.cache = cache
        execution.promoted |= promoted
        execution.input_chars = max(execution.input_chars, input_chars)
        execution.model_attempts += int(model_attempt)


def current_analysis_id() -> str | None:
    execution = _execution.get()
    return execution.analysis_id if execution is not None else None


def analysis_queue_remaining(settings: Settings) -> float:
    execution = _execution.get()
    if execution is None:
        return settings.llm_queue_timeout_seconds
    return execution.queue_budget - sum(
        execution.stages.get(stage, 0) for stage in ("same_version_wait", "capacity_wait")
    )
