import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import process_lock, report_deadline, request_cancellation


def test_infrastructure_has_only_core_entry_points():
    root = Path(__file__).resolve().parents[2]
    modules = ('request_cancellation', 'report_deadline', 'process_lock')
    retired = tuple(f'app.services.{name}' for name in modules)
    for name in modules:
        assert not (root / 'backend/app/services' / f'{name}.py').exists()
    paths = [path for folder in ('backend/app', 'backend/tests', 'scripts')
             for path in (root / folder).rglob('*.py')]
    paths.extend(root.glob('*.py'))
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
            references = []
            if isinstance(node, ast.Import):
                references = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                references = [f'{node.module}.{alias.name}' for alias in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                references = [node.value]
            assert not any(ref == old or ref.startswith(old + '.') for ref in references for old in retired), path


def test_cancellation_context_and_cursor_share_the_registered_execution():
    registry = request_cancellation.sql_request_cancellations
    execution = registry.activate('core-execution')
    token = request_cancellation.bind_request_id(execution)
    events = []
    cursor = SimpleNamespace(cancel=lambda: events.append('cancel'), close=lambda: events.append('close'))
    try:
        with pytest.raises(request_cancellation.SqlRequestCancelled):
            with request_cancellation.cancellable_cursor(SimpleNamespace(cursor=lambda: cursor)):
                assert registry.cancel(execution)
        assert events == ['cancel', 'close']
    finally:
        request_cancellation.reset_request_id(token)
        registry.finish(execution)
    assert request_cancellation.current_request_id.get() is None


def test_deadline_is_enforced_and_restores_context():
    with report_deadline.report_deadline(0):
        with pytest.raises(report_deadline.ReportOperationTimeout):
            report_deadline.ensure_time_remaining()
    assert report_deadline.remaining_seconds() is None


def test_file_locks_share_reentrant_state(tmp_path):
    path = tmp_path / 'settings.env'
    with process_lock.settings_file_lock(path), process_lock.settings_file_lock(path):
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
