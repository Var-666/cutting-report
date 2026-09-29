import json

import pytest

from app.config import Settings
from app.database.llm_usage import LlmUsageRepository
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.llm_usage_service import record_llm_call
from app.services.llm_service import LlmService


class Response:
    def __init__(self, usage):
        self.usage = usage

    def json(self):
        return {"usage": self.usage}


def test_usage_counts_attempts_but_never_estimates_missing_tokens(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    record_llm_call(settings, Response({"total_tokens": 31}))
    record_llm_call(settings, Response({"prompt_tokens": 10, "completion_tokens": 4}))
    record_llm_call(settings, Response(None))
    record_llm_call(settings, None)

    summary = LlmUsageRepository(settings.metadata_db_path).summary()
    assert summary == {"call_count": 4, "metered_count": 2, "total_tokens": 45}


def test_report_model_request_is_counted_even_when_provider_omits_usage(tmp_path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path, llm_enabled=True,
        llm_api_key="test", llm_base_url="http://llm.test",
    )

    class ModelResponse:
        def raise_for_status(self):
            pass

        def json(self):
            content = {key: "分析" for key in ("overview", "features", "alerts", "suggestions")}
            return {"choices": [{"message": {"content": json.dumps(content)}}]}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            return ModelResponse()

    monkeypatch.setattr("app.services.llm_service.httpx.Client", Client)
    LlmService(settings).analyze_context("leadership", {"facts": []})
    assert LlmUsageRepository(settings.metadata_db_path).summary() == {
        "call_count": 1, "metered_count": 0, "total_tokens": 0,
    }


def test_alarm_model_request_adds_provider_token_usage(tmp_path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path, llm_enabled=True,
        llm_api_key="test", llm_base_url="http://llm.test",
    )

    class ModelResponse:
        def raise_for_status(self):
            pass

        def json(self):
            content = {
                "overall_risk": "warning", "summary": "无告警", "findings": [],
                "actions": [], "limitations": [],
            }
            return {
                "choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            return ModelResponse()

    monkeypatch.setattr("app.services.alarm_analysis_service.httpx.Client", Client)
    AlarmAnalysisService(settings).analyze([])
    assert LlmUsageRepository(settings.metadata_db_path).summary() == {
        "call_count": 1, "metered_count": 1, "total_tokens": 9,
    }


def test_failed_http_attempt_counts_without_inventing_tokens(tmp_path, monkeypatch) -> None:
    settings = Settings(
        project_root=tmp_path, llm_enabled=True,
        llm_api_key="test", llm_base_url="http://llm.test",
    )

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            raise RuntimeError("connection failed")

    monkeypatch.setattr("app.services.llm_service.httpx.Client", Client)
    with pytest.raises(RuntimeError, match="connection failed"):
        LlmService(settings).analyze_context("leadership", {"facts": []})
    assert LlmUsageRepository(settings.metadata_db_path).summary() == {
        "call_count": 1, "metered_count": 0, "total_tokens": 0,
    }
