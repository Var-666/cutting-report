from __future__ import annotations

import json
import logging
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from time import monotonic, sleep
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.database.postgres import PostgresDatabase
from app.models.domain import AnalysisResult
from app.services.llm_prompts import analysis_system_prompt
from app.services.llm_usage_service import record_llm_call
from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations
from app.core.report_deadline import ensure_time_remaining, operation_deadline, remaining_seconds
from app.services.analysis_execution import analysis_observation, analysis_queue_remaining, analysis_stage, current_analysis_id
from app.services.model_coordination import model_slot


logger = logging.getLogger(__name__)


REQUIRED_FIELDS = ("overview", "features", "alerts", "suggestions")


class AnalysisInputTooLarge(ValueError):
    """Facts/tables exceed the configured model input budget."""


class AnalysisCapacityError(RuntimeError):
    """The bounded shared model queue is full or expired."""


class LlmRequestTimeout(TimeoutError):
    """The complete model response exceeded the configured wall-clock budget."""


_BACKGROUND_LLM: ContextVar[bool] = ContextVar("background_llm", default=False)
_GENERATION_PRIORITY: ContextVar[threading.Event | None] = ContextVar("generation_priority", default=None)


def is_background_llm_work() -> bool:
    return _BACKGROUND_LLM.get()


@contextmanager
def generation_priority(foreground_waiting: threading.Event):
    token = _GENERATION_PRIORITY.set(foreground_waiting)
    try:
        yield
    finally:
        _GENERATION_PRIORITY.reset(token)


@contextmanager
def background_llm_work():
    """Mark automatic refresh/monitor calls without changing their service APIs."""
    token = _BACKGROUND_LLM.set(True)
    try:
        yield
    finally:
        _BACKGROUND_LLM.reset(token)


class _LlmCapacity:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active = 0
        self._background_active = 0
        self._foreground_waiting = 0

    def acquire(self, settings: Settings, *, background: bool) -> bool:
        deadline = monotonic() + analysis_queue_remaining(settings)
        priority = _GENERATION_PRIORITY.get()
        registered_foreground = False
        try:
            while True:
                ensure_time_remaining()
                request_id = current_request_id.get()
                if sql_request_cancellations.is_cancelled(request_id):
                    raise SqlRequestCancelled("智能分析请求已取消")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise AnalysisCapacityError("智能分析排队超时，请稍后重试。")
                report_remaining = remaining_seconds()
                if report_remaining is not None:
                    remaining = min(remaining, max(0.001, report_remaining))
                with self._condition:
                    effective_background = background and not (priority and priority.is_set())
                    if registered_foreground != (not effective_background):
                        self._foreground_waiting += -1 if registered_foreground else 1
                        registered_foreground = not effective_background
                        self._condition.notify_all()
                    # With a single configured slot, priority still applies but
                    # an already running request cannot be preempted.
                    background_available = (
                        self._background_active < max(1, settings.llm_max_concurrent // 2)
                        and self._foreground_waiting == 0
                    )
                    if self._active < settings.llm_max_concurrent and (not effective_background or background_available):
                        self._active += 1
                        if effective_background:
                            self._background_active += 1
                        analysis_observation(promoted=background and not effective_background)
                        return effective_background
                    self._condition.wait(timeout=min(0.1, remaining))
        finally:
            if registered_foreground:
                with self._condition:
                    self._foreground_waiting -= 1
                    self._condition.notify_all()

    def release(self, *, background: bool) -> None:
        with self._condition:
            self._active -= 1
            if background:
                self._background_active -= 1
            self._condition.notify_all()


_LLM_CAPACITY = _LlmCapacity()


@contextmanager
def llm_capacity(settings: Settings, database: PostgresDatabase):
    background = _BACKGROUND_LLM.get()
    priority = _GENERATION_PRIORITY.get()
    with model_slot(settings, database, background=background, promoted=lambda: bool(priority and priority.is_set()),
                    budget=analysis_queue_remaining(settings)) as slot:
        with analysis_stage("capacity_wait"):
            admitted_background = _LLM_CAPACITY.acquire(settings, background=background)
            try:
                slot.acquire()
            except BaseException:
                _LLM_CAPACITY.release(background=admitted_background)
                raise
        try:
            yield
        finally:
            _LLM_CAPACITY.release(background=admitted_background)


def post_llm_json(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
    max_response_bytes: int,
) -> tuple[Any, dict[str, Any]]:
    """Read a bounded response, checking cancellation/deadlines at chunk boundaries."""
    ensure_time_remaining()
    report_remaining = remaining_seconds()
    if report_remaining is not None:
        timeout_seconds = min(timeout_seconds, max(0.001, report_remaining))
    deadline = monotonic() + timeout_seconds

    def check_active() -> None:
        ensure_time_remaining()
        if sql_request_cancellations.is_cancelled(current_request_id.get()):
            raise SqlRequestCancelled("智能分析请求已取消")
        if monotonic() >= deadline:
            raise LlmRequestTimeout("大模型响应超过总时限，请稍后重试。")

    check_active()
    # Check the shared deadline between chunks; an in-flight blocking read
    # still relies on httpx's socket timeout and cannot be killed by this loop.
    if hasattr(client, "stream"):
        content = bytearray()
        remaining = max(0.001, deadline - monotonic())
        with client.stream(
            "POST", url, headers=headers, json=payload,
            timeout=httpx.Timeout(remaining, connect=min(10.0, remaining)),
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                check_active()
                if len(content) + len(chunk) > max_response_bytes:
                    raise AnalysisOutputValidationError("大模型响应体超过配置容量")
                content.extend(chunk)
            check_active()
        try:
            body = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnalysisOutputValidationError("大模型未返回有效 JSON") from exc
        if not isinstance(body, dict):
            raise AnalysisOutputValidationError("大模型未返回 JSON 对象")
        check_active()
        return response, body

    # Small test doubles and older adapters may only expose post().
    response = client.post(url, headers=headers, json=payload)
    check_active()
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise AnalysisOutputValidationError("大模型未返回 JSON 对象")
    if len(json.dumps(body, ensure_ascii=True).encode("utf-8")) > max_response_bytes:
        raise AnalysisOutputValidationError("大模型响应体超过配置容量")
    check_active()
    return response, body


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    value = response.headers.get("Retry-After", "").strip()
    try:
        if value.isascii() and value.isdigit():
            return float(int(value))
        if value:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        pass
    return 5 * 2 ** (attempt - 1) + uniform(0, 1)


def _wait_for_retry(delay: float) -> None:
    deadline = monotonic() + delay
    while True:
        ensure_time_remaining()
        if sql_request_cancellations.is_cancelled(current_request_id.get()):
            raise SqlRequestCancelled("智能分析请求已取消")
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleep(min(0.1, remaining))


def request_llm_json(
    settings: Settings, client: httpx.Client, url: str, *, headers: dict[str, str],
    payload: dict[str, Any], scene: str, database: PostgresDatabase,
) -> dict[str, Any]:
    """Retry only explicit throttling; share capacity, deadline and usage accounting."""
    analysis_id = current_analysis_id() or uuid4().hex
    budget = settings.llm_queue_timeout_seconds + settings.llm_timeout
    outer_remaining = remaining_seconds()
    if outer_remaining is not None:
        budget = min(budget, outer_remaining)
    attempts = 0
    outcome = "success"
    try:
        with operation_deadline(budget):
            for attempt in range(1, 4):
                try:
                    with llm_capacity(settings, database):
                        ensure_time_remaining()
                        if sql_request_cancellations.is_cancelled(current_request_id.get()):
                            raise SqlRequestCancelled("智能分析请求已取消")
                        response = response_body = None
                        occurred_at = datetime.now(ZoneInfo(settings.timezone))
                        started = monotonic()
                        attempts += 1
                        analysis_observation(model_attempt=True)
                        error_type, status, upstream_request_id = "-", None, "-"
                        try:
                            with analysis_stage("model"):
                                response, response_body = post_llm_json(
                                    client, url, headers=headers, payload=payload,
                                    timeout_seconds=settings.llm_timeout,
                                    # Allow escaped surrogate pairs and provider envelope.
                                    max_response_bytes=12 * settings.llm_max_output_chars + 65536,
                                )
                            status = getattr(response, "status_code", None)
                            return response_body
                        except BaseException as exc:
                            error_type = type(exc).__name__
                            if isinstance(exc, httpx.HTTPStatusError):
                                status = exc.response.status_code
                                upstream_request_id = re.sub(
                                    r"[^a-zA-Z0-9._:-]", "", exc.response.headers.get("x-request-id", "")
                                )[:128] or "-"
                            raise
                        finally:
                            duration_ms = round((monotonic() - started) * 1000)
                            record_llm_call(settings, database, response, response_body, scene=scene,
                                            occurred_at=occurred_at, duration_ms=duration_ms)
                            logger.info(
                                "model HTTP attempt: analysis_id=%s request_id=%s device=%s scene=%s attempt=%d "
                                "upstream_status=%s error_type=%s duration_ms=%d upstream_request_id=%s",
                                analysis_id, current_request_id.get() or "-", settings.device_id, scene,
                                attempt, status, error_type, duration_ms, upstream_request_id,
                            )
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 429 or attempt == 3:
                        raise
                    delay = _retry_delay(exc.response, attempt)
                    remaining = remaining_seconds()
                    if remaining is not None and delay >= remaining:
                        logger.warning("model retry skipped: analysis_id=%s reason=deadline attempts=%d", analysis_id, attempts)
                        raise
                    logger.warning("model retry scheduled: analysis_id=%s attempt=%d wait_ms=%d upstream_status=429",
                                   analysis_id, attempt + 1, round(delay * 1000))
                    with analysis_stage("retry_wait"):
                        _wait_for_retry(delay)
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        logger.info("model request completed: analysis_id=%s device=%s scene=%s attempts=%d outcome=%s",
                    analysis_id, settings.device_id, scene, attempts, outcome)


class AnalysisOutputValidationError(ValueError):
    """The model response cannot populate the four analysis sections."""


def _required_content(content: Any) -> bool:
    return isinstance(content, dict) and all(
        isinstance(content.get(key), str) and content[key].strip()
        for key in REQUIRED_FIELDS
    )


class LlmService:
    def __init__(self, settings: Settings, database: PostgresDatabase) -> None:
        self.settings = settings
        self.database = database

    def analyze_context(self, report_type: str, context: dict[str, Any]) -> AnalysisResult:
        if not self.settings.llm_enabled:
            raise RuntimeError("LLM service is disabled")
        user_content = json.dumps(
            {
                "report_type": report_type,
                "report_date": context.get("report_date"),
                "report_month": context.get("report_month"),
                "facts": context.get("facts", []),
                "tables": context.get("tables", []),
            }, ensure_ascii=False,
        )
        if len(user_content) > self.settings.llm_max_input_chars:
            raise AnalysisInputTooLarge("智能分析输入超过配置容量，请缩小报表范围后重试。")
        analysis_observation(input_chars=len(user_content))
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": analysis_system_prompt(report_type)},
                {"role": "user", "content": user_content},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        url = self.settings.llm_base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self.settings.llm_timeout) as client:
            response_body = request_llm_json(self.settings, client, url, headers=headers,
                                            database=self.database, payload=payload, scene=report_type)
        raw_content = response_body["choices"][0]["message"]["content"]
        if not isinstance(raw_content, str) or len(raw_content) > self.settings.llm_max_output_chars:
            raise AnalysisOutputValidationError("大模型返回内容超过配置容量")
        try:
            content = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AnalysisOutputValidationError("大模型未返回可展示的 JSON 分析结果") from exc
        if not _required_content(content):
            raise AnalysisOutputValidationError("大模型分析结果缺少页面所需的四段内容")
        return AnalysisResult(
            status="success",
            overview=content["overview"],
            features=content["features"],
            alerts=content["alerts"],
            suggestions=content["suggestions"],
            mode="model",
        )
