from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
import time

import pytest
import httpx

from app.config import Settings
from app.services.llm_service import (
    AnalysisCapacityError, AnalysisInputTooLarge, AnalysisOutputValidationError, LlmRequestTimeout, LlmService,
    _LlmCapacity, background_llm_work, llm_capacity, post_llm_json,
)
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.request_cancellation import (
    SqlRequestCancelled, bind_request_id, reset_request_id, sql_request_cancellations,
)
from test_worker_auth_service import _enabled_settings
from app.services.report_deadline import ReportOperationTimeout, report_deadline


def config(tmp_path, **changes):
    return Settings(project_root=tmp_path, llm_enabled=True, llm_api_key='key',
                    llm_base_url='http://llm.local', **changes)


def test_report_input_budget_rejects_before_network(tmp_path, monkeypatch):
    service = LlmService(config(tmp_path, llm_max_input_chars=1000))
    def fail(*args, **kwargs):
        raise AssertionError('network must not be called for oversized input')
    monkeypatch.setattr('app.services.llm_service.httpx.Client', fail)
    with pytest.raises(AnalysisInputTooLarge):
        service.analyze_context('leadership', {'tables': [{'x': 'x' * 2000}]})


def test_alarm_input_budget_rejects_before_network(tmp_path, monkeypatch):
    settings = config(tmp_path, llm_max_input_chars=1000)
    service = AlarmAnalysisService(settings)
    monkeypatch.setattr('app.services.alarm_analysis_service.httpx.Client',
                        lambda **kwargs: (_ for _ in ()).throw(AssertionError('network called')))
    incident = {'incident_id': 'i', 'machine_no': 'm', 'alarm_type': 't', 'alarm_code': 'c' * 2000,
                'alarm_class': 'a', 'alarm_desc': 'd', 'severity': 'warning', 'status': 'open',
                'first_start_time': None, 'first_seen_at': 'now', 'latest_remove_time': None}
    with pytest.raises(AnalysisInputTooLarge):
        service.analyze([incident])


@pytest.mark.parametrize('limit', [1, 10])
def test_capacity_limits_concurrent_model_calls(tmp_path, limit):
    settings = config(tmp_path, llm_max_concurrent=limit, llm_queue_timeout_seconds=1)
    entered = [Event() for _ in range(limit)]
    release = Event()
    def task(index):
        with llm_capacity(settings):
            entered[index].set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=limit) as executor:
        futures = [executor.submit(task, index) for index in range(limit)]
        try:
            assert all(event.wait(1) for event in entered), 'all configured slots must be usable'
            with pytest.raises(AnalysisCapacityError):
                with llm_capacity(settings):
                    pytest.fail('an extra call must not exceed the shared model limit')
        finally:
            release.set()
        for future in futures:
            future.result(timeout=2)
    with llm_capacity(settings):
        pass


def test_capacity_cancellation_does_not_consume_slot(tmp_path):
    settings = config(tmp_path, llm_max_concurrent=1, llm_queue_timeout_seconds=2)
    with llm_capacity(settings):
        execution = sql_request_cancellations.activate('llm')
        token = bind_request_id(execution)
        try:
            sql_request_cancellations.cancel(execution)
            with pytest.raises(SqlRequestCancelled):
                with llm_capacity(settings):
                    pass
        finally:
            reset_request_id(token); sql_request_cancellations.finish(execution)
    with llm_capacity(settings):
        pass


def test_two_devices_background_calls_leave_two_slots_for_foreground(tmp_path, monkeypatch):
    capacity = _LlmCapacity()
    monkeypatch.setattr('app.services.llm_service._LLM_CAPACITY', capacity)
    settings = config(tmp_path, llm_max_concurrent=4, llm_queue_timeout_seconds=2)
    background_entered = [Event(), Event(), Event()]
    foreground_entered = [Event(), Event()]
    release = Event()

    def background(index):
        device = replace(settings, device_id=f'Cutter0{index + 1}')
        with background_llm_work(), llm_capacity(device):
            background_entered[index].set()
            assert release.wait(3)

    def foreground(index):
        with llm_capacity(settings):
            foreground_entered[index].set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(background, index) for index in range(2)]
        try:
            assert all(event.wait(1) for event in background_entered[:2])
            futures.append(executor.submit(background, 2))
            futures.extend(executor.submit(foreground, index) for index in range(2))
            assert all(event.wait(1) for event in foreground_entered)
            assert not background_entered[2].is_set()
        finally:
            release.set()
        for future in futures:
            future.result(timeout=2)
    assert background_entered[2].is_set()
    assert capacity._active == capacity._background_active == capacity._foreground_waiting == 0


def test_foreground_waiter_precedes_background_even_with_one_configured_slot(tmp_path, monkeypatch):
    capacity = _LlmCapacity()
    monkeypatch.setattr('app.services.llm_service._LLM_CAPACITY', capacity)
    settings = config(tmp_path, llm_max_concurrent=1, llm_queue_timeout_seconds=2)
    order = []

    def background():
        with background_llm_work(), llm_capacity(settings):
            order.append('background')

    def foreground():
        with llm_capacity(settings):
            order.append('foreground')

    with ThreadPoolExecutor(max_workers=2) as executor:
        with llm_capacity(settings):
            bg = executor.submit(background)
            fg = executor.submit(foreground)
            with capacity._condition:
                assert capacity._condition.wait_for(lambda: capacity._foreground_waiting == 1, timeout=1)
        fg.result(timeout=2)
        bg.result(timeout=2)
    assert order == ['foreground', 'background']


def test_cancelled_foreground_does_not_leave_background_permanently_blocked(tmp_path, monkeypatch):
    capacity = _LlmCapacity()
    monkeypatch.setattr('app.services.llm_service._LLM_CAPACITY', capacity)
    settings = config(tmp_path, llm_max_concurrent=1, llm_queue_timeout_seconds=2)
    with llm_capacity(settings):
        execution = sql_request_cancellations.activate('foreground')
        token = bind_request_id(execution)
        try:
            sql_request_cancellations.cancel(execution)
            with pytest.raises(SqlRequestCancelled):
                with llm_capacity(settings):
                    pytest.fail('cancelled waiter must not acquire capacity')
        finally:
            reset_request_id(token)
            sql_request_cancellations.finish(execution)
    with background_llm_work(), llm_capacity(settings):
        assert capacity._background_active == 1
    with llm_capacity(settings):
        assert capacity._background_active == 0


@pytest.mark.parametrize('kwargs', [
    {'llm_max_input_chars': 999}, {'llm_max_output_chars': 999},
    {'llm_max_concurrent': 0}, {'llm_max_concurrent': 11},
    {'llm_queue_timeout_seconds': 0}, {'llm_queue_timeout_seconds': 301},
    {'llm_timeout': 0}, {'llm_timeout': 301},
])
def test_llm_limits_are_validated(tmp_path, kwargs):
    with pytest.raises(ValueError):
        config(tmp_path, **kwargs)


def test_model_output_budget_rejects_oversized_content(tmp_path, monkeypatch):
    settings = config(tmp_path, llm_max_output_chars=1000)
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "x" * 1001}}]}
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr("app.services.llm_service.httpx.Client", Client)
    with pytest.raises(Exception, match="超过配置容量"):
        LlmService(settings).analyze_context("leadership", {"facts": []})


def test_streaming_model_response_has_one_total_deadline():
    class Response:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            time.sleep(0.02)
            yield b"{}"

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *args):
            pass

    class Client:
        def stream(self, *args, **kwargs):
            return Stream()

    with pytest.raises(LlmRequestTimeout):
        post_llm_json(
            Client(), "http://llm.local/chat/completions", headers={}, payload={},
            timeout_seconds=0.001,
            max_response_bytes=1024,
        )


def test_streaming_model_response_is_parsed_after_body_is_consumed():
    class Response:
        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b'{"choices": '
            yield b'[]}'

    class Stream:
        def __enter__(self):
            return Response()

        def __exit__(self, *args):
            pass

    class Client:
        def stream(self, *args, **kwargs):
            return Stream()

    _response, body = post_llm_json(
        Client(), "http://llm.local/chat/completions", headers={}, payload={},
        timeout_seconds=1,
        max_response_bytes=1024,
    )
    assert body == {"choices": []}


def test_response_envelope_is_bounded_before_full_buffering():
    class Body(httpx.SyncByteStream):
        consumed = 0
        closed = False

        def __iter__(self):
            for _ in range(100):
                self.consumed += 1
                yield b" " * 100

        def close(self):
            self.closed = True

    body = Body()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=body))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(AnalysisOutputValidationError, match="响应体超过"):
            post_llm_json(client, "http://llm.local", headers={}, payload={},
                          timeout_seconds=1, max_response_bytes=150)
    assert body.consumed == 2
    assert body.closed


def test_response_cancellation_stops_consuming_and_closes_stream():
    execution = sql_request_cancellations.activate("stream")
    token = bind_request_id(execution)

    class Body(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield b'{"choices": '
            sql_request_cancellations.cancel(execution)
            yield b'[]}'
            raise AssertionError("cancelled response must not continue reading")

        def close(self):
            self.closed = True

    body = Body()
    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=body))
    try:
        with httpx.Client(transport=transport) as client:
            with pytest.raises(SqlRequestCancelled):
                post_llm_json(client, "http://llm.local", headers={}, payload={},
                              timeout_seconds=1, max_response_bytes=1024)
        assert body.closed
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution)


def test_model_call_uses_report_remaining_budget():
    timeouts = []

    def respond(request):
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with report_deadline(0.5):
            post_llm_json(client, "http://llm.local", headers={}, payload={},
                          timeout_seconds=30, max_response_bytes=1024)
    assert all(0 < value <= 0.5 for value in timeouts[0].values())


def test_capacity_wait_obeys_report_deadline(tmp_path):
    settings = config(tmp_path, llm_max_concurrent=1)
    with llm_capacity(settings):
        with report_deadline(0.02):
            with pytest.raises(ReportOperationTimeout):
                with llm_capacity(settings):
                    pytest.fail("occupied slot must not be acquired")
    with llm_capacity(settings):
        pass


def test_alarm_output_budget_is_checked_before_json_parsing(tmp_path, monkeypatch):
    settings = config(tmp_path, llm_max_output_chars=1000)
    original_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": "x" * 1001}}]},
    ))
    monkeypatch.setattr("app.services.alarm_analysis_service.httpx.Client",
                        lambda **kwargs: original_client(transport=transport, **kwargs))
    with pytest.raises(AnalysisOutputValidationError, match="超过配置容量"):
        AlarmAnalysisService(settings).analyze([])
