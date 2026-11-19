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

from app.core.operations_logging import event
from app.config import Settings
from app.database.postgres import PostgresDatabase
from app.models.domain import AnalysisResult
from app.models.analysis_brief import BriefInput
from app.services.llm_prompts import analysis_system_prompt
from app.services.report_analysis.brief import model_context
from app.services.report_analysis.brief_validation import validate_brief, validate_welding_quantities
from app.services.llm_usage_service import record_llm_call
from app.core.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations
from app.core.report_deadline import ReportOperationTimeout, ensure_time_remaining, operation_deadline, remaining_seconds
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
        if isinstance(exc, Exception) and not isinstance(exc, SqlRequestCancelled):
            code = 'MODEL_REQUEST_FAILED'
            if isinstance(exc, httpx.HTTPStatusError):
                code = {401: 'MODEL_AUTH_FAILED', 403: 'MODEL_AUTH_FAILED',
                        429: 'MODEL_RATE_LIMITED'}.get(exc.response.status_code, code)
            elif isinstance(exc, (TimeoutError, httpx.TimeoutException, ReportOperationTimeout)):
                code = 'MODEL_TIMEOUT'
            elif isinstance(exc, AnalysisCapacityError):
                code = 'MODEL_CAPACITY'
            event(logger, logging.WARNING if code in ('MODEL_RATE_LIMITED', 'MODEL_CAPACITY') else logging.ERROR,
                  '模型请求失败', error_code=code, analysis_id=analysis_id, line_id=settings.device_id,
                  stage=scene, resource_id=scene, incident=True, exc_info=True)
        raise
    finally:
        logger.info("model request completed: analysis_id=%s device=%s scene=%s attempts=%d outcome=%s",
                    analysis_id, settings.device_id, scene, attempts, outcome)


class AnalysisOutputValidationError(ValueError):
    """The model response cannot populate the four analysis sections."""


def analysis_error_message(error: Exception) -> str:
    """Describe failures without exposing provider bodies, URLs or credentials."""
    if isinstance(error, (AnalysisInputTooLarge, AnalysisCapacityError)):
        return str(error)
    if isinstance(error, ReportOperationTimeout):
        return "智能分析处理超时（包含数据准备、排队和模型调用），请稍后重试。"
    if isinstance(error, (LlmRequestTimeout, httpx.TimeoutException, TimeoutError)):
        return "模型响应超时，请稍后重试。"
    if isinstance(error, AnalysisOutputValidationError):
        return "模型返回的分析未通过格式或数据校验，请重试。"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 429:
            return "模型接口限流，请稍后重试。"
        if status in {401, 403}:
            return "模型接口认证失败，请管理员检查密钥和访问权限。"
        return f"模型接口返回错误（HTTP {status}），请稍后重试。"
    if isinstance(error, httpx.HTTPError):
        return "无法连接模型接口，请检查网络或联系管理员。"
    return "大模型智能分析暂不可用，请稍后重试。"


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
        cutting = report_type in {'leadership', 'workshop', 'equipment'}
        model_input = model_context(context) if cutting else {
                "report_type": report_type,
                "report_date": context.get("report_date"),
                "report_month": context.get("report_month"),
                "facts": context.get("facts", []),
                "tables": context.get("tables", []),
            }
        if cutting:
            schema = BriefInput.model_json_schema()
            finding = schema['$defs']['BriefFindingInput']['properties']
            finding.pop('evidence_ids')
            candidate_ids = [item['id'] for item in model_input['candidates']]
            if candidate_ids:
                finding['candidate_id']['enum'] = candidate_ids
                schema['$defs']['BriefAction']['properties']['candidate_id']['enum'] = candidate_ids
            else:
                schema['properties']['findings']['maxItems'] = 0
                schema['properties']['actions']['maxItems'] = 0
            fact_ids = [item['id'] for item in model_input['facts']]
            if fact_ids:
                schema['properties']['key_metric_ids']['items']['enum'] = fact_ids
            else:
                schema['properties']['key_metric_ids']['maxItems'] = 0
            model_input['output_schema'] = schema
        user_content = json.dumps(model_input, ensure_ascii=False)
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
        with operation_deadline(self.settings.llm_queue_timeout_seconds + self.settings.llm_timeout), \
                httpx.Client(timeout=self.settings.llm_timeout) as client:
            for attempt in range(2):
                response_body = request_llm_json(self.settings, client, url, headers=headers,
                                                database=self.database, payload=payload, scene=report_type)
                try:
                    raw_content = response_body['choices'][0]['message']['content']
                except (KeyError, IndexError, TypeError) as exc:
                    raise AnalysisOutputValidationError('大模型响应缺少分析正文') from exc
                try:
                    result = self._analysis_result(report_type, context, raw_content)
                    logger.info('model output validated: analysis_id=%s device=%s scene=%s output_attempts=%d',
                                current_analysis_id() or '-', self.settings.device_id, report_type, attempt + 1)
                    return result
                except AnalysisOutputValidationError as exc:
                    event(logger, logging.ERROR if attempt else logging.WARNING, '模型输出校验失败',
                          error_code='MODEL_OUTPUT_INVALID',
                          analysis_id=current_analysis_id(), line_id=self.settings.device_id,
                          stage=report_type, resource_id=report_type)
                    if attempt or not isinstance(raw_content, str):
                        raise
                    feedback = ('上次输出未通过校验。只修正以下问题，重新返回符合output_schema的完整JSON；'
                                '不要改变权威数据，不要照抄错误数值。切割简报的业务数字和时长使用{{F001}}形式的事实占位符，'
                                '不适合引用的数字请改用不带数字的准确表述；省略evidence_ids，由后端绑定。'
                                f'校验原因：{str(exc)[:600]}') if cutting else (
                                '上次输出未通过校验，请仅基于原始数据重新返回JSON，包含四个非空字符串'
                                f'overview、features、alerts、suggestions。校验原因：{str(exc)[:600]}')
                    if len(user_content) + len(raw_content) + len(feedback) > self.settings.llm_max_input_chars:
                        raise
                    ensure_time_remaining()
                    logger.warning('model output correction scheduled: analysis_id=%s device=%s scene=%s reason=%s',
                                   current_analysis_id() or '-', self.settings.device_id, report_type, str(exc)[:600])
                    payload['messages'].extend([{'role': 'assistant', 'content': raw_content},
                                                {'role': 'user', 'content': feedback}])
                    analysis_observation(input_chars=len(user_content) + len(raw_content) + len(feedback))

    def _analysis_result(self, report_type: str, context: dict[str, Any], raw_content: Any) -> AnalysisResult:
        if not isinstance(raw_content, str) or len(raw_content) > self.settings.llm_max_output_chars:
            raise AnalysisOutputValidationError("大模型返回内容超过配置容量")
        try:
            content = json.loads(raw_content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AnalysisOutputValidationError("大模型未返回可展示的 JSON 分析结果") from exc
        if report_type in {'leadership', 'workshop', 'equipment'}:
            try:
                brief = validate_brief(content, context)
            except ValueError as exc:
                raise AnalysisOutputValidationError(f"大模型简报校验失败：{exc}") from exc
            return AnalysisResult(
                status='success', mode='model', brief=brief,
                overview=brief['summary'],
                features='\n'.join(f"{item['title']}：{item['detail']}" for item in brief['findings']),
                alerts='',
                suggestions='\n'.join(f"{item['title']}：{item['detail']}" for item in brief['actions']),
            )
        if not _required_content(content):
            raise AnalysisOutputValidationError("大模型分析结果缺少页面所需的四段内容")
        if report_type == 'welding':
            try:
                validate_welding_quantities(content, context)
            except ValueError as exc:
                raise AnalysisOutputValidationError(str(exc)) from exc
        return AnalysisResult(
            status="success",
            overview=content["overview"],
            features=content["features"],
            alerts=content["alerts"],
            suggestions=content["suggestions"],
            mode="model",
        )
