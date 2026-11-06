from dataclasses import replace
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_admin, require_user
from app.api.cutting import router
from tests.dispatch_fixtures import dispatch_service as _service


@pytest.fixture
def history(state_database, tmp_path):
    service, repository, admin = _service(state_database, tmp_path)
    service.pdm.get_file = lambda *_: pytest.fail('history must not query PDM')
    service.magic_client_factory = lambda *_: pytest.fail('history must not query the gateway')
    def add(*, sent_at=None, created_at='2026-11-01T08:00:00+08:00', machine='10.0.0.1',
            simulation=False, quantity=1, status='sent'):
        item = repository.create(dict(document_id='same-file', project_id='P1', document_no='A.GEN',
            relative_path='P1/A.GEN', version_id='V1', machine_ip=machine, quantity=quantity,
            status='ready', source_mode='sqlserver', is_simulation=simulation), user=admin, now=created_at)
        if sent_at:
            item = repository.transition(item['dispatch_id'], expected={'ready'}, status='sent', now=sent_at, stage='complete')
            if status != 'sent':
                item = repository.transition(item['dispatch_id'], expected={'sent'}, status=status,
                                             now='2026-11-03T10:00:00+08:00')
        return item
    return service, repository, add


def test_daily_counts_match_paginated_details_without_counting_retries_simulation_or_quantity(history):
    service, repository, add = history
    first = add(sent_at='2026-11-02T08:00:00+08:00', quantity=10)
    second = add(sent_at='2026-11-02T09:00:00+08:00', machine='10.0.0.2')
    cancelled = add(sent_at='2026-11-02T10:00:00+08:00', status='cancelled')
    utc = add(sent_at='2026-11-01T16:30:00+00:00')
    add(sent_at='2026-11-02T11:00:00+08:00', simulation=True)
    add()
    failed = add()
    repository.transition(failed['dispatch_id'], expected={'ready'}, status='push_failed',
                          now='2026-11-02T12:00:00+08:00', stage='upload_unknown')
    # A rejected cancellation restores sent state but must keep the first success date.
    repository.transition(first['dispatch_id'], expected={'sent'}, status='pushing', now='2026-11-03T10:00:00+08:00')
    restored = repository.transition(first['dispatch_id'], expected={'pushing'}, status='sent', now='2026-11-03T10:01:00+08:00')
    assert restored['sent_at'] == first['sent_at']
    filters = dict(start_date=date(2026, 11, 1), end_date=date(2026, 11, 3))
    trend = service.dispatch_trend(**filters)
    assert trend['items'] == [{'date': '2026-11-01', 'sent_count': 0, 'completed_count': 0},
                              {'date': '2026-11-02', 'sent_count': 4, 'completed_count': 0},
                              {'date': '2026-11-03', 'sent_count': 0, 'completed_count': 0}]
    assert trend['machine_ips'] == ['10.0.0.1', '10.0.0.2']
    pages = [service.list_dispatches(page=page, page_size=2, **filters) for page in (1, 2)]
    assert all(page['total'] == 4 for page in pages)
    assert {row['dispatch_id'] for page in pages for row in page['items']} == {
        item['dispatch_id'] for item in (first, second, cancelled, utc)}
    assert service.list_dispatches(page=1, page_size=20)['total'] == 7
    selected = {**filters, 'machine_ip': '10.0.0.2'}
    assert service.dispatch_trend(**selected)['items'][1]['sent_count'] == 1
    assert service.list_dispatches(page=1, page_size=20, **selected)['items'][0]['dispatch_id'] == second['dispatch_id']
    assert service.list_dispatches(page=1, page_size=20, start_date=date(2026, 11, 3), end_date=date(2026, 11, 3))['total'] == 0


def test_success_date_uses_configured_timezone_and_daylight_saving_boundaries(history):
    service, _, add = history
    service.settings = replace(service.settings, timezone='America/New_York')
    add(sent_at='2026-11-01T00:30:00-04:00')
    add(sent_at='2026-11-01T23:30:00-05:00')
    add(sent_at='2026-11-02T00:00:00-05:00')
    filters = dict(start_date=date(2026, 11, 1), end_date=date(2026, 11, 1))
    assert service.dispatch_trend(**filters)['items'] == [{'date': '2026-11-01', 'sent_count': 2, 'completed_count': 0}]
    assert service.list_dispatches(page=1, page_size=20, **filters)['total'] == 2


def test_dispatch_trend_http_contract_filters_validation_and_admin_scope(history):
    service, _, add = history
    item = add(sent_at='2026-11-02T09:00:00+08:00')
    app = FastAPI()
    app.include_router(router)
    app.state.cutting_dispatch = service
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'dispatch-trend-test'
        return await call_next(request)
    with TestClient(app) as client:
        params = {'start_date': '2026-11-02', 'end_date': '2026-11-02', 'machine_ip': '10.0.0.1'}
        response = client.get('/api/v1/cutting/dispatches/trend', params=params)
        assert response.status_code == 200
        assert response.json()['data']['items'] == [{'date': '2026-11-02', 'sent_count': 1, 'completed_count': 0}]
        details = client.get('/api/v1/cutting/dispatches', params=params).json()['data']
        assert details['total'] == 1 and details['items'][0]['dispatch_id'] == item['dispatch_id']
        assert client.get('/api/v1/cutting/dispatches/' + item['dispatch_id']).status_code == 200
        for values in [dict(start_date='2026-11-03', end_date='2026-11-01'),
                       dict(start_date='2026-11-02'), dict(start_date='2025-01-01', end_date='2026-11-01'),
                       dict(start_date='1800-01-01', end_date='1800-01-01'),
                       dict(start_date='2026-02-30', end_date='2026-03-01')]:
            assert client.get('/api/v1/cutting/dispatches', params=values).status_code == 422
        app.dependency_overrides.clear()
        app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
        assert client.get('/api/v1/cutting/dispatches/trend', params=params).status_code == 403
