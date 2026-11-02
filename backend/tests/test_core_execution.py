from types import SimpleNamespace

import pytest

from app.core import process_lock, report_deadline, request_cancellation
from app.services import process_lock as legacy_lock
from app.services import report_deadline as legacy_deadline
from app.services import request_cancellation as legacy_cancellation


def test_legacy_and_core_cancellation_share_registry_context_and_exception():
    assert legacy_cancellation.sql_request_cancellations is request_cancellation.sql_request_cancellations
    assert legacy_cancellation.current_request_id is request_cancellation.current_request_id
    assert legacy_cancellation.SqlRequestCancelled is request_cancellation.SqlRequestCancelled
    registry = request_cancellation.sql_request_cancellations
    execution = registry.activate('core-compatibility')
    token = legacy_cancellation.bind_request_id(execution)
    events = []
    cursor = SimpleNamespace(cancel=lambda: events.append('cancel'), close=lambda: events.append('close'))
    try:
        with pytest.raises(legacy_cancellation.SqlRequestCancelled):
            with request_cancellation.cancellable_cursor(SimpleNamespace(cursor=lambda: cursor)):
                assert legacy_cancellation.sql_request_cancellations.cancel(execution)
        assert events == ['cancel', 'close']
    finally:
        request_cancellation.reset_request_id(token)
        registry.finish(execution)
    assert legacy_cancellation.current_request_id.get() is None


def test_legacy_deadline_is_enforced_in_core_and_restores_context():
    assert legacy_deadline.ReportOperationTimeout is report_deadline.ReportOperationTimeout
    with legacy_deadline.report_deadline(0):
        with pytest.raises(legacy_deadline.ReportOperationTimeout):
            report_deadline.ensure_time_remaining()
    assert report_deadline.remaining_seconds() is None
    assert legacy_deadline.operation_deadline is report_deadline.operation_deadline


def test_legacy_and_core_file_locks_share_reentrant_state(tmp_path):
    assert legacy_lock.FileLock is process_lock.FileLock
    assert legacy_lock.settings_file_lock is process_lock.settings_file_lock
    path = tmp_path / 'settings.env'
    with process_lock.settings_file_lock(path), legacy_lock.settings_file_lock(path):
        contender = process_lock.FileLock(path.with_name(path.name + '.lock'))
        assert not contender.acquire(blocking=False)
    assert contender.acquire(blocking=False)
    contender.release()


def test_http_execution_context_reaches_query_thread_and_is_cleaned_up():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.workloads import query_requests
    from app.main import request_id_middleware

    app = FastAPI()
    app.middleware('http')(request_id_middleware)
    observed = []

    @app.get('/context')
    async def context():
        def query():
            execution = request_cancellation.current_request_id.get()
            observed.append(execution)
            return request_cancellation.sql_request_cancellations._requests[execution]['client_request_id']
        return await query_requests.run_sync(query)

    with TestClient(app) as client:
        response = client.get('/context', headers={'X-Request-ID': 'core-context'})
    assert response.status_code == 200 and response.json() == 'core-context'
    assert response.headers['X-Request-ID'] == 'core-context'
    assert observed[0] not in request_cancellation.sql_request_cancellations._requests
