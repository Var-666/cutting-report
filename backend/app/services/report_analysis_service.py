from __future__ import annotations

import hashlib
import json
import logging
import threading
from contextlib import contextmanager, nullcontext
from datetime import date, datetime
from functools import wraps
from time import monotonic, time
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.models.domain import (
    AnalysisResult,
    EquipmentRecord,
    LeadershipReportResult,
    WorkshopRecord,
    to_primitive,
)
from app.services.llm_service import (
    AnalysisCapacityError, AnalysisInputTooLarge, AnalysisOutputValidationError,
    LlmRequestTimeout, LlmService, generation_priority, is_background_llm_work,
)
from app.services.analysis_execution import (
    analysis_observation, analysis_operation, analysis_prepared, analysis_queue_remaining, analysis_stage,
)
from app.services.report_deadline import ReportOperationTimeout, ensure_time_remaining, remaining_seconds
from app.services.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations

from app.services.report_analysis.common import (
    _plain_language, generate_data_version, normalize_analysis_context,
)
from app.services.report_analysis.leadership import build_context as build_leadership_context
from app.services.report_analysis.workshop import build_context as build_workshop_context
from app.services.report_analysis.equipment import build_context as build_equipment_context


logger = logging.getLogger(__name__)
PROMPT_VERSION = "report-analysis-v22-full-report-tables"


def _present_analysis(result: AnalysisResult) -> AnalysisResult:
    """Translate display copy without changing source statistics."""
    for field in ("overview", "features", "alerts", "suggestions"):
        setattr(result, field, _plain_language(getattr(result, field)))
    result.facts = [
        {
            key: _plain_language(value) if key in {"label", "value", "source"} and isinstance(value, str) else value
            for key, value in fact.items()
        }
        for fact in result.facts
    ]
    return result


def analysis_period_label(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} 至 {end.isoformat()}"


class AnalysisDataVersionMismatch(ValueError):
    """The report shown by the browser is no longer the authoritative version."""


class AnalysisUnavailableError(RuntimeError):
    """The model did not produce a usable analysis result."""


def leadership_analysis_source_version(report: LeadershipReportResult | dict) -> str:
    data = to_primitive(report)
    relevant = {
        key: data.get(key)
        for key in (
            "report_month",
            "period_start",
            "period_end",
            "daily_records",
            "monthly_records",
            "current_month_total",
            "comparison_total",
            "comparison_start",
            "comparison_end",
            "year_ago_total",
            "project_distribution",
        )
    }
    return generate_data_version(relevant)


def workshop_analysis_source_version(report_date: str, records: list) -> str:
    def task_id(row) -> str:
        if isinstance(row, dict):
            return str(row.get("task_id") or "")
        return str(getattr(row, "task_id", ""))

    ordered = sorted(records, key=task_id)
    return generate_data_version(
        {"report_date": report_date, "records": to_primitive(ordered)}
    )


def equipment_analysis_source_version(task_report: dict) -> str:
    relevant = {
        key: task_report.get(key)
        for key in (
            "report_date",
            "period_start",
            "period_end",
            "records",
            "summary",
            "units",
            "equipment_scope",
            "energy_notice",
        )
    }
    relevant["records"] = sorted(
        task_report.get("records") or [],
        key=lambda row: str(row.get("task_id") or ""),
    )
    relevant["equipment_scope"] = sorted(task_report.get("equipment_scope") or [])
    return generate_data_version(relevant)


def analysis_table_source_version(rows: list) -> str:
    """Return a stable version for one complete analysis table."""
    return generate_data_version({"rows": to_primitive(rows)})


def combined_analysis_source_version(primary: str, secondary: str | None = None) -> str:
    """Join both visible report-table versions into one analysis version."""
    return f"{primary}.{secondary or primary}"


def _require_current_data_version(expected: str | None, actual: str) -> None:
    if expected and expected != actual:
        raise AnalysisDataVersionMismatch(
            "报表数据已更新，请刷新当前报表后重新生成智能分析。"
        )


def _report_analysis_operation(report_type: str):
    def decorate(method):
        @wraps(method)
        def wrapped(self, *args, **kwargs):
            value = args[0] if args else kwargs.get("report", kwargs.get("report_date", ""))
            report_date = str(value.report_month) if report_type == "leadership" else str(value)
            with analysis_operation(self.settings, report_type, report_date,
                                    background=is_background_llm_work()):
                return method(self, *args, **kwargs)
        return wrapped
    return decorate


class ReportAnalysisService:
    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        self.settings = settings
        self.metadata = metadata
        self.llm = LlmService(settings)
        self.timezone = ZoneInfo(settings.timezone)
        self._generation_guard = threading.Lock()
        self._generation_locks: dict[str, dict] = {}

    @contextmanager
    def _process_generation(self, cache_key: str, priority):
        from app.services.process_lock import FileLock
        lock = FileLock(self.settings.metadata_db_path.parent / ".coordination" / f"analysis-{cache_key}.lock")
        priority_path = lock.path.with_suffix(".foreground.lock")
        foreground_lock = FileLock(priority_path) if not is_background_llm_work() else None

        class SharedPriority:
            def is_set(self):
                if priority.is_set():
                    return True
                probe = FileLock(priority_path)
                if probe.acquire(blocking=False):
                    probe.release()
                    return False
                return True

        deadline = monotonic() + analysis_queue_remaining(self.settings)
        waited = False
        try:
            if foreground_lock:
                foreground_lock.acquire(blocking=False)
            with analysis_stage("same_version_wait"):
                while not lock.acquire(blocking=False):
                    waited = True
                    if foreground_lock and foreground_lock.handle is None:
                        foreground_lock.acquire(blocking=False)
                    ensure_time_remaining()
                    if sql_request_cancellations.is_cancelled(current_request_id.get()):
                        raise SqlRequestCancelled("智能分析请求已取消")
                    if monotonic() >= deadline:
                        raise AnalysisUnavailableError("智能分析排队超时，请稍后重试。")
                    threading.Event().wait(0.05)
            previous = self.metadata.generation(cache_key) if waited else None
            if previous and previous["status"] == "failed":
                error_type = {"AnalysisUnavailableError": AnalysisUnavailableError,
                              "ReportOperationTimeout": ReportOperationTimeout}[previous["error_type"]]
                raise error_type(previous["error_message"])
            shared = bool(previous and previous["status"] == "completed")
            attempt = uuid4().hex
            if not shared:
                self.metadata.start_generation(cache_key, attempt, time())
            status, error_type, message = "completed", None, None
            try:
                with generation_priority(SharedPriority()):
                    yield shared
            except (AnalysisUnavailableError, ReportOperationTimeout) as exc:
                status, error_type, message = "failed", type(exc).__name__, str(exc)
                raise
            except BaseException:
                status = "interrupted"
                raise
            finally:
                if not shared:
                    self.metadata.finish_generation(cache_key, attempt, status, time(), error_type, message)
        finally:
            lock.release()
            if foreground_lock:
                foreground_lock.release()

    @contextmanager
    def _single_generation(self, cache_key: str):
        """Coalesce concurrent generation for one version inside this process."""
        with self._generation_guard:
            state = self._generation_locks.get(cache_key)
            if state is None:
                state = {"lock": threading.Lock(), "users": 0, "foreground_users": 0,
                         "priority": threading.Event(), "error": None}
                self._generation_locks[cache_key] = state
            was_inflight = state["users"] > 0
            state["users"] += 1
            foreground = not is_background_llm_work()
            if foreground:
                state["foreground_users"] += 1
                state["priority"].set()
            # Reserve the first producer while registering it, so a later forced
            # caller cannot overtake it and cause two generations of one version.
            acquired = state["lock"].acquire(blocking=False)
        deadline = monotonic() + analysis_queue_remaining(self.settings)
        try:
            with analysis_stage("same_version_wait"):
                while not acquired:
                    ensure_time_remaining()
                    if sql_request_cancellations.is_cancelled(current_request_id.get()):
                        raise SqlRequestCancelled("智能分析请求已取消")
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise AnalysisUnavailableError("智能分析排队超时，请稍后重试。")
                    report_remaining = remaining_seconds()
                    if report_remaining is not None:
                        remaining = min(remaining, max(0.001, report_remaining))
                    acquired = state["lock"].acquire(timeout=min(0.1, remaining))
            ensure_time_remaining()
            if sql_request_cancellations.is_cancelled(current_request_id.get()):
                raise SqlRequestCancelled("智能分析请求已取消")
            if was_inflight and state["error"] is not None:
                # Share this attempt's failure only with its current waiters.
                # Once they leave, a later explicit retry creates a fresh state.
                error_type, message = state["error"]
                raise error_type(message)
            with self._process_generation(cache_key, state["priority"]) as cross_process_wait:
                try:
                    yield was_inflight or cross_process_wait
                except (AnalysisUnavailableError, ReportOperationTimeout) as exc:
                    state["error"] = (type(exc), str(exc))
                    raise
        finally:
            if acquired:
                state["lock"].release()
            with self._generation_guard:
                if foreground:
                    state["foreground_users"] -= 1
                    if state["foreground_users"] == 0:
                        state["priority"].clear()
                state["users"] -= 1
                if state["users"] == 0:
                    self._generation_locks.pop(cache_key, None)

    def latest_default_analysis(self, report_type: str, report_date: date) -> AnalysisResult | None:
        lookup_date = report_date.strftime("%Y-%m") if report_type == "leadership" else report_date.isoformat()
        cached = self.metadata.get_latest_analysis(
            report_type=report_type, report_date=lookup_date,
            model_name=self.settings.llm_model, prompt_version=PROMPT_VERSION,
        )
        return self._cached_result(cached) if cached else None

    @staticmethod
    def _cached_result(cached: dict) -> AnalysisResult:
        allowed = {key: value for key, value in cached.items() if key in AnalysisResult.__dataclass_fields__}
        allowed["cached"] = True
        return _present_analysis(AnalysisResult(**allowed))

    def _get_cached_analysis(
        self,
        *,
        cache_key: str,
        report_type: str,
        report_date: str,
        data_version: str,
    ) -> AnalysisResult | None:
        with analysis_stage("cache"):
            cached = self.metadata.get_analysis(
                cache_key,
                report_type=report_type,
                report_date=report_date,
                data_version=data_version,
            )
        if not cached:
            return None
        cached["data_version"] = data_version
        analysis_observation(cache="hit")
        return self._cached_result(cached)

    def _analyze(
        self,
        *,
        report_type: str,
        report_date: str,
        context: dict,
        force: bool,
        source_data_version: str | None = None,
        publish_latest: bool = False,
    ) -> AnalysisResult:
        if not self.settings.llm_enabled:
            raise AnalysisUnavailableError(
                "大模型智能分析未启用，请先完成配置。"
            )
        primitive_context, canonical = normalize_analysis_context(context)
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        data_version = source_data_version or input_hash
        engine = f"model:{self.settings.llm_model}"
        cache_key = hashlib.sha256(
            f"{self.settings.device_id}|{PROMPT_VERSION}|{report_type}|{report_date}|{engine}|{data_version}|{input_hash}".encode(
                "utf-8"
            )
        ).hexdigest()
        analysis_prepared()
        self.metadata.save_analysis_source_version(
            report_type=report_type,
            report_date=report_date,
            data_version=data_version,
            updated_at=datetime.now(self.timezone).isoformat(timespec="seconds"),
        )
        if not force:
            cached = self._get_cached_analysis(
                cache_key=cache_key,
                report_type=report_type,
                report_date=report_date,
                data_version=data_version,
            )
            if cached:
                return cached

        # Periodic rounds must each call the model even if an older round for
        # this exact input is still running. Page/manual callers keep single-flight.
        generation = (nullcontext(False) if force and is_background_llm_work()
                      else self._single_generation(cache_key))
        with generation as waited_for_same_version:
            # Always recheck after acquiring the key lock. Normal requests share the
            # cache; simultaneous forced requests share the result generated by the
            # first waiter, while a later manual refresh still generates a new result.
            if not force or waited_for_same_version:
                cached = self._get_cached_analysis(
                    cache_key=cache_key,
                    report_type=report_type,
                    report_date=report_date,
                    data_version=data_version,
                )
                if cached:
                    if waited_for_same_version:
                        analysis_observation(cache="shared")
                    return cached

            try:
                result = self.llm.analyze_context(report_type, primitive_context)
                ensure_time_remaining()
                if result.mode != "model":
                    raise AnalysisOutputValidationError(
                        "LLM 分析结果未标记为模型输出"
                    )
            except (SqlRequestCancelled, ReportOperationTimeout):
                raise
            except AnalysisInputTooLarge as exc:
                raise AnalysisUnavailableError(str(exc)) from exc
            except (AnalysisCapacityError, LlmRequestTimeout, httpx.HTTPError) as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                logger.warning(
                    "report model request failed: device=%s report_type=%s report_date=%s error_type=%s upstream_status=%s",
                    self.settings.device_id, report_type, report_date, type(exc).__name__, status,
                )
                if isinstance(exc, AnalysisCapacityError):
                    message = str(exc)
                elif isinstance(exc, (LlmRequestTimeout, httpx.TimeoutException)):
                    message = "模型响应超时，请稍后重试。"
                elif status == 429:
                    message = "模型接口限流，请稍后重试。"
                elif status in {401, 403}:
                    message = "模型接口认证失败，请管理员检查密钥和访问权限。"
                elif status is not None:
                    message = f"模型接口返回错误（HTTP {status}），请稍后重试。"
                else:
                    message = "无法连接模型接口，请检查网络或联系管理员。"
                raise AnalysisUnavailableError(message) from exc
            except AnalysisOutputValidationError as exc:
                logger.warning("LLM analysis response cannot be displayed: %s", exc)
                raise AnalysisUnavailableError(
                    "大模型未返回可展示的分析结果，请重试。"
                ) from exc
            except Exception as exc:
                logger.exception("LLM analysis failed")
                raise AnalysisUnavailableError(
                    "大模型智能分析暂不可用，请稍后重试。"
                ) from exc

            result.facts = primitive_context.get("facts", [])
            _present_analysis(result)
            result.data_version = data_version
            result.generated_at = datetime.now(self.timezone).isoformat(timespec="seconds")
            result.cached = False
            analysis_observation(cache="generated")
            payload = to_primitive(result)
            self.metadata.save_analysis(
                cache_key=cache_key,
                report_type=report_type,
                report_date=report_date,
                input_hash=input_hash,
                data_version=data_version,
                status=result.status,
                mode="model",
                model_name=self.settings.llm_model,
                prompt_version=PROMPT_VERSION,
                result=payload,
                generated_at=result.generated_at,
                publish_latest=publish_latest,
            )
            return result

    @_report_analysis_operation("leadership")
    def analyze_leadership(
        self,
        report: LeadershipReportResult,
        *,
        monthly_records: list | None = None,
        expected_data_version: str | None = None,
        force: bool = False,
        publish_latest: bool = False,
    ) -> AnalysisResult:
        summary_rows = report.monthly_records if monthly_records is None else monthly_records
        source_data_version = combined_analysis_source_version(
            leadership_analysis_source_version(report),
            analysis_table_source_version(summary_rows),
        )
        _require_current_data_version(expected_data_version, source_data_version)
        context = build_leadership_context(report, summary_rows)
        return self._analyze(
            report_type="leadership",
            report_date=context["report_month"],
            context=context,
            force=force,
            source_data_version=source_data_version,
            publish_latest=publish_latest,
        )

    @_report_analysis_operation("workshop")
    def analyze_workshop(
        self,
        report_date: str,
        records: list[WorkshopRecord],
        *,
        comparison_records: list[WorkshopRecord] | None = None,
        comparison_date: str | None = None,
        year_ago_records: list[WorkshopRecord] | None = None,
        year_ago_date: str | None = None,
        expected_data_version: str | None = None,
        force: bool = False,
        publish_latest: bool = False,
    ) -> AnalysisResult:
        source_data_version = combined_analysis_source_version(
            workshop_analysis_source_version(report_date, records)
        )
        _require_current_data_version(expected_data_version, source_data_version)
        context = build_workshop_context(
            report_date, records,
            comparison_records=comparison_records,
            comparison_date=comparison_date,
            year_ago_records=year_ago_records,
            year_ago_date=year_ago_date,
        )
        return self._analyze(
            report_type="workshop",
            report_date=report_date,
            context=context,
            force=force,
            source_data_version=source_data_version,
            publish_latest=publish_latest,
        )

    @_report_analysis_operation("equipment")
    def analyze_equipment(
        self,
        report_date: str,
        records: list[EquipmentRecord],
        *,
        energy_data_mode: str = "unavailable",
        energy_units: dict[str, str] | None = None,
        energy_notice: str = "",
        energy_summary_rows: list[dict] | None = None,
        comparison_records: list[EquipmentRecord] | None = None,
        comparison_date: str | None = None,
        year_ago_records: list[EquipmentRecord] | None = None,
        year_ago_date: str | None = None,
        source_data_version: str | None = None,
        expected_data_version: str | None = None,
        force: bool = False,
        publish_latest: bool = False,
    ) -> AnalysisResult:
        if source_data_version is not None:
            source_data_version = combined_analysis_source_version(
                source_data_version,
                analysis_table_source_version(energy_summary_rows or []),
            )
            _require_current_data_version(expected_data_version, source_data_version)
        context = build_equipment_context(
            report_date, records,
            energy_data_mode=energy_data_mode,
            energy_units=energy_units,
            energy_notice=energy_notice,
            energy_summary_rows=energy_summary_rows,
            comparison_records=comparison_records,
            comparison_date=comparison_date,
            year_ago_records=year_ago_records,
            year_ago_date=year_ago_date,
        )
        return self._analyze(
            report_type="equipment",
            report_date=report_date,
            context=context,
            force=force,
            source_data_version=source_data_version,
            publish_latest=publish_latest,
        )
