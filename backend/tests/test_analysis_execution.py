import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import analysis as analysis_api
from app.config import Settings
from app.services import analysis_execution, llm_service, report_deadline
from app.services.analysis_execution import (
    analysis_observation, analysis_operation, analysis_prepared, analysis_queue_remaining, analysis_stage,
)


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=100.0)
    for module in (analysis_execution, llm_service, report_deadline):
        monkeypatch.setattr(module, "monotonic", lambda: clock.now)
    return clock


def test_preparation_and_both_queues_reduce_one_total_response_budget(tmp_path, clock):
    settings = Settings(project_root=tmp_path, llm_queue_timeout_seconds=120, llm_timeout=90)
    with analysis_operation(settings, "leadership", "2026-10"):
        clock.now += 30  # Data preparation counts toward the end-to-end deadline.
        analysis_prepared()
        with analysis_stage("same_version_wait"):
            clock.now += 80
        assert analysis_queue_remaining(settings) == 40
        with analysis_stage("capacity_wait"):
            clock.now += 30
        assert analysis_queue_remaining(settings) == 10

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_bytes(self): yield b"{}"

        class Client:
            def stream(self, *args, **kwargs):
                # No fresh 90-second allowance after spending 140 seconds.
                assert kwargs["timeout"].read == 70
                assert kwargs["timeout"].connect == 10
                return Response()

        llm_service.post_llm_json(Client(), "http://llm.test", headers={}, payload={},
                                 timeout_seconds=90, max_response_bytes=100)
    assert report_deadline.remaining_seconds() is None


def test_exhausted_shared_queue_budget_never_starts_another_model(tmp_path, clock):
    settings = Settings(project_root=tmp_path, llm_queue_timeout_seconds=120)
    with analysis_operation(settings, "leadership", "2026-10"):
        with analysis_stage("same_version_wait"):
            clock.now += 120
        capacity = llm_service._LlmCapacity()
        with pytest.raises(llm_service.AnalysisCapacityError):
            capacity.acquire(settings, background=False)
        assert capacity._active == capacity._foreground_waiting == 0


def test_nested_analysis_preserves_shorter_export_deadline(tmp_path, clock):
    settings = Settings(project_root=tmp_path)
    with report_deadline.report_deadline(5):
        with analysis_operation(settings, "leadership", "2026-10"):
            clock.now += 3
            with analysis_operation(settings, "leadership", "2026-10"):
                assert report_deadline.remaining_seconds() == 2
        assert report_deadline.remaining_seconds() == 2
    assert report_deadline.remaining_seconds() is None


@pytest.mark.parametrize("fails", [False, True])
def test_timing_log_accounts_for_waits_on_success_and_failure(tmp_path, clock, caplog, fails):
    settings = Settings(project_root=tmp_path)
    caplog.set_level(logging.INFO, logger=analysis_execution.__name__)
    try:
        with analysis_operation(settings, "leadership", "2026-10", request_id="timing-request"):
            clock.now += 2
            analysis_prepared()
            for stage, seconds in (("cache", 1), ("same_version_wait", 3), ("capacity_wait", 4), ("model", 5)):
                with analysis_stage(stage):
                    clock.now += seconds
            analysis_observation(cache="generated", promoted=True, input_chars=123)
            if fails:
                raise ValueError("sensitive model content must not be logged")
    except ValueError:
        pass
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    for field in ("request_id=timing-request", "device=Cutter01", "total_ms=15000", "prepare_ms=2000",
                  "cache_ms=1000", "same_version_wait_ms=3000", "capacity_wait_ms=4000", "model_ms=5000",
                  "finalize_ms=0", "promoted=True", "input_chars=123"):
        assert field in message
    assert f"outcome={'ValueError' if fails else 'success'}" in message
    assert "sensitive" not in message


def test_analysis_api_includes_data_preparation_in_deadline_and_reports_timeout(tmp_path, clock, monkeypatch):
    settings = Settings(project_root=tmp_path, llm_queue_timeout_seconds=120, llm_timeout=90)
    state = SimpleNamespace(report_analysis_service=SimpleNamespace(settings=settings))
    monkeypatch.setattr(analysis_api, "device_state", lambda request: state)

    def prepare_data(payload, request):
        assert report_deadline.remaining_seconds() == 210
        clock.now += 211
        return {"success": True}

    monkeypatch.setattr(analysis_api, "_analyze_report", prepare_data)
    payload = analysis_api.AnalysisRequest(report_type="leadership", report_date="2026-10-01")
    with pytest.raises(HTTPException) as error:
        analysis_api.analyze_report(payload, SimpleNamespace(state=SimpleNamespace(request_id="api-request")))
    assert error.value.status_code == 503
    assert "总等待时限" in error.value.detail
    assert report_deadline.remaining_seconds() is None
