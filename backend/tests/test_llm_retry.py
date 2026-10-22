import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from threading import Event, Lock
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.database.llm_usage import LlmUsageRepository
from app.services import analysis_execution, llm_service, report_deadline
from app.services.alarm_analysis_service import AlarmAnalysisService
from app.services.analysis_execution import analysis_operation
from app.services.llm_service import AnalysisOutputValidationError, LlmService, _LlmCapacity
from app.services.request_cancellation import (
    SqlRequestCancelled, bind_request_id, reset_request_id, sql_request_cancellations,
)


@pytest.fixture
def clock(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    for module in (llm_service, analysis_execution, report_deadline):
        monkeypatch.setattr(module, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(llm_service, 'sleep', lambda seconds: setattr(clock, 'now', clock.now + seconds))
    monkeypatch.setattr(llm_service, 'uniform', lambda *_args: 0)
    return clock


@pytest.fixture
def settings(tmp_path):
    return Settings(project_root=tmp_path, llm_enabled=True, llm_base_url='http://llm.test',
                    llm_api_key='secret-key-must-not-be-logged', llm_max_concurrent=10, llm_timeout=180)


def mock_http(monkeypatch, handler):
    original_client = httpx.Client
    monkeypatch.setattr(llm_service.httpx, 'Client',
                        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))


def envelope(scenario='report'):
    content = ({key: '分析' for key in ('overview', 'features', 'alerts', 'suggestions')}
               if scenario == 'report' else {'summary': '无告警', 'findings': [], 'actions': [], 'limitations': []})
    return {'choices': [{'message': {'content': json.dumps(content)}}], 'usage': {'total_tokens': 9}}


def analyze(settings, scenario='report'):
    if scenario == 'report':
        return LlmService(settings).analyze_context('workshop', {'facts': []})
    return AlarmAnalysisService(settings).analyze([])


@pytest.mark.parametrize('scenario', ['report', 'alarm'])
def test_throttled_calls_recover_with_honest_usage_and_no_capacity_held_during_backoff(
    settings, clock, monkeypatch, caplog, scenario,
):
    capacity = _LlmCapacity()
    monkeypatch.setattr(llm_service, '_LLM_CAPACITY', capacity)
    caplog.set_level(logging.INFO)
    requests, responses, sent_at = [], [], []
    sleeps = []
    original_sleep = llm_service.sleep

    def sleep(seconds):
        assert capacity._active == 0, 'throttle backoff must leave capacity for other rounds'
        sleeps.append(seconds)
        original_sleep(seconds)

    monkeypatch.setattr(llm_service, 'sleep', sleep)

    def respond(request):
        assert capacity._active == 1
        requests.append(json.loads(request.content))
        sent_at.append(clock.now)
        if len(requests) < 3:
            response = httpx.Response(429, headers={'Retry-After': str(len(requests) + 1), 'x-request-id': 'provider-429'},
                                      json={'error': 'sensitive upstream body'})
        else:
            response = httpx.Response(200, json=envelope(scenario))
        responses.append(response)
        return response

    mock_http(monkeypatch, respond)
    analyze(settings, scenario)
    assert sent_at == [1000, 1002, 1005], 'each retry must respect the provider wait'
    assert requests[0] == requests[1] == requests[2]
    assert all(response.is_closed for response in responses)
    assert sum(sleeps) == pytest.approx(5)
    assert capacity._active == capacity._background_active == capacity._foreground_waiting == 0
    assert LlmUsageRepository(settings.metadata_db_path).summary() == {
        'call_count': 3, 'metered_count': 1, 'total_tokens': 9,
    }
    with LlmUsageRepository(settings.metadata_db_path).connect() as connection:
        rows = connection.execute('SELECT status, scene FROM llm_usage_calls ORDER BY id').fetchall()
    assert [row['status'] for row in rows] == ['failed', 'failed', 'responded']
    assert {row['scene'] for row in rows} == {'workshop' if scenario == 'report' else 'alarm'}
    logs = [record.getMessage() for record in caplog.records if record.name == llm_service.__name__]
    assert len([line for line in logs if line.startswith('model HTTP attempt:')]) == 3
    assert 'attempts=3 outcome=success' in logs[-1]
    assert 'upstream_request_id=provider-429' in caplog.text
    assert 'sensitive upstream body' not in caplog.text
    assert settings.llm_api_key not in caplog.text


@pytest.mark.parametrize('scenario', ['report', 'alarm'])
def test_persistent_throttling_stops_after_two_retries(settings, clock, monkeypatch, scenario):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(429)

    mock_http(monkeypatch, respond)
    with pytest.raises(httpx.HTTPStatusError) as error:
        analyze(settings, scenario)
    assert error.value.response.status_code == 429
    assert len(requests) == 3
    assert clock.now == 1015, 'fallback waits grow from five to ten seconds'
    assert LlmUsageRepository(settings.metadata_db_path).summary()['call_count'] == 3


@pytest.mark.parametrize('failure', [401, 403, 500, 503, 'read_timeout', 'connect_error'])
def test_only_explicit_throttling_is_retried(settings, clock, monkeypatch, failure):
    requests = []

    def respond(request):
        requests.append(request)
        if failure == 'read_timeout':
            raise httpx.ReadTimeout('read timed out', request=request)
        if failure == 'connect_error':
            raise httpx.ConnectError('connection failed', request=request)
        return httpx.Response(failure)

    mock_http(monkeypatch, respond)
    with pytest.raises(httpx.HTTPError):
        analyze(settings)
    assert len(requests) == 1, 'an ambiguous timeout must not duplicate upstream generation'
    assert clock.now == 1000
    assert LlmUsageRepository(settings.metadata_db_path).summary()['call_count'] == 1


@pytest.mark.parametrize('malformed', ['envelope', 'analysis'])
def test_invalid_model_output_does_not_trigger_more_generation(settings, clock, monkeypatch, malformed):
    requests = []

    def respond(request):
        requests.append(request)
        if malformed == 'envelope':
            return httpx.Response(200, text='not JSON')
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    mock_http(monkeypatch, respond)
    with pytest.raises(AnalysisOutputValidationError):
        analyze(settings)
    assert len(requests) == 1


@pytest.mark.parametrize('value, expected', [('7', 7), ('0', 0), ('-1', 5), ('invalid', 5), ('', 5)])
def test_retry_after_seconds_or_invalid_header_fallback(clock, value, expected):
    assert llm_service._retry_delay(httpx.Response(429, headers={'Retry-After': value}), 1) == expected


def test_retry_after_http_date_is_respected(clock):
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=60), usegmt=True)
    assert 59 <= llm_service._retry_delay(httpx.Response(429, headers={'Retry-After': future}), 1) <= 60
    past = format_datetime(datetime.now(timezone.utc) - timedelta(seconds=60), usegmt=True)
    assert llm_service._retry_delay(httpx.Response(429, headers={'Retry-After': past}), 1) == 0


def test_retry_is_not_sent_early_when_retry_after_exceeds_remaining_budget(settings, clock, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(429, headers={'Retry-After': '100'})

    mock_http(monkeypatch, respond)
    with report_deadline.report_deadline(3):
        with pytest.raises(httpx.HTTPStatusError):
            analyze(settings)
    assert len(requests) == 1
    assert clock.now == 1000


def test_retry_uses_remaining_round_budget_and_logs_final_analysis_result(settings, clock, monkeypatch, caplog):
    timeouts = []
    caplog.set_level(logging.INFO)

    def respond(request):
        timeouts.append(request.extensions['timeout'])
        clock.now += 2 if len(timeouts) == 1 else 3
        if len(timeouts) == 1:
            return httpx.Response(429, headers={'Retry-After': '2'})
        return httpx.Response(200, json=envelope())

    mock_http(monkeypatch, respond)
    with report_deadline.report_deadline(8):
        with analysis_operation(settings, 'workshop', '2026-10-19'):
            analyze(settings)
            assert report_deadline.remaining_seconds() == 1
    assert [item['read'] for item in timeouts] == [8, 4]
    message = next(record.getMessage() for record in caplog.records if 'report analysis timing:' in record.getMessage())
    assert 'outcome=success' in message
    assert 'model_attempts=2 retry_wait_ms=2000' in message
    assert 'model_ms=5000' in message
    assert 'total_ms=7000' in message
    ids = {part.split('=', 1)[1] for record in caplog.records
           if record.name in (llm_service.__name__, analysis_execution.__name__)
           for part in record.getMessage().split() if part.startswith('analysis_id=')}
    assert len(ids) == 1, 'attempts and final analysis outcome must be traceable to the same round'


def test_cancellation_during_backoff_prevents_another_paid_attempt(settings, clock, monkeypatch):
    execution = sql_request_cancellations.activate('retry')
    token = bind_request_id(execution)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(429, headers={'Retry-After': '5'})

    def sleep(seconds):
        clock.now += seconds
        sql_request_cancellations.cancel(execution)

    mock_http(monkeypatch, respond)
    monkeypatch.setattr(llm_service, 'sleep', sleep)
    try:
        with pytest.raises(SqlRequestCancelled):
            analyze(settings)
    finally:
        reset_request_id(token)
        sql_request_cancellations.finish(execution)
    assert len(requests) == 1
    assert LlmUsageRepository(settings.metadata_db_path).summary()['call_count'] == 1


def test_another_round_can_finish_while_previous_round_is_in_backoff(settings, clock, monkeypatch):
    waiting, release, second_done = Event(), Event(), Event()
    lock = Lock()
    requests = []

    def respond(request):
        with lock:
            requests.append(request)
            first = len(requests) == 1
        return httpx.Response(429) if first else httpx.Response(200, json=envelope())

    def wait(_delay):
        waiting.set()
        assert release.wait(3)

    def second():
        analyze(settings)
        second_done.set()

    mock_http(monkeypatch, respond)
    monkeypatch.setattr(llm_service, '_wait_for_retry', wait)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(analyze, settings)
        try:
            assert waiting.wait(1)
            following = executor.submit(second)
            assert second_done.wait(1), 'a throttled round must not stall the following round'
        finally:
            release.set()
        first.result(timeout=2)
        following.result(timeout=2)
    assert len(requests) == 3
    assert LlmUsageRepository(settings.metadata_db_path).summary()['call_count'] == 3
