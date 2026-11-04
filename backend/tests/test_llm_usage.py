import json

import pytest
import httpx

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


def test_usage_counts_attempts_but_never_estimates_missing_tokens(state_database, tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    record_llm_call(settings, state_database, Response({"total_tokens": 31}))
    record_llm_call(settings, state_database, Response({"prompt_tokens": 10, "completion_tokens": 4}))
    record_llm_call(settings, state_database, Response(None))
    record_llm_call(settings, state_database, None)

    summary = LlmUsageRepository(state_database).summary()
    assert summary == {"call_count": 4, "metered_count": 2, "total_tokens": 45}


def test_report_model_request_is_counted_even_when_provider_omits_usage(state_database, tmp_path, monkeypatch) -> None:
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
    LlmService(settings, database=state_database).analyze_context("leadership", {"facts": []})
    assert LlmUsageRepository(state_database).summary() == {
        "call_count": 1, "metered_count": 0, "total_tokens": 0,
    }


def test_alarm_model_request_adds_provider_token_usage(state_database, tmp_path, monkeypatch) -> None:
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
    AlarmAnalysisService(settings, database=state_database).analyze([])
    assert LlmUsageRepository(state_database).summary() == {
        "call_count": 1, "metered_count": 1, "total_tokens": 9,
    }


def test_failed_http_attempt_counts_without_inventing_tokens(state_database, tmp_path, monkeypatch) -> None:
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
        LlmService(settings, database=state_database).analyze_context("leadership", {"facts": []})
    assert LlmUsageRepository(state_database).summary() == {
        "call_count": 1, "metered_count": 0, "total_tokens": 0,
    }


@pytest.mark.parametrize("scenario", ["report", "alarm"])
def test_real_streamed_model_response_succeeds_and_records_usage(state_database, tmp_path, monkeypatch, scenario):
    settings = Settings(project_root=tmp_path, llm_enabled=True,
                        llm_api_key="test", llm_base_url="http://llm.test")
    if scenario == "report":
        content = {key: "分析" for key in ("overview", "features", "alerts", "suggestions")}
    else:
        content = {"summary": "无告警", "findings": [], "actions": [], "limitations": []}
    envelope = {"choices": [{"message": {"content": json.dumps(content)}}],
                "usage": {"total_tokens": 9}}

    class Body(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield json.dumps(envelope).encode()

        def close(self):
            self.closed = True

    body = Body()
    response = httpx.Response(200, stream=body)
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: response)
    monkeypatch.setattr("app.services.llm_service.httpx.Client",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    if scenario == "report":
        assert LlmService(settings, database=state_database).analyze_context("leadership", {"facts": []}).status == "success"
    else:
        assert AlarmAnalysisService(settings, database=state_database).analyze([])["summary"] == "无告警"
    assert body.closed
    with pytest.raises(httpx.ResponseNotRead):
        response.json()
    assert LlmUsageRepository(state_database).summary() == {
        "call_count": 1, "metered_count": 1, "total_tokens": 9,
    }
