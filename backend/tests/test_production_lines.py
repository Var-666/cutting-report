from types import SimpleNamespace

import pytest

from app.services import production_lines


def test_registration_exposes_uniform_public_metadata_and_uses_each_service(monkeypatch):
    second = {'id': 'weld-2', 'name': '第二焊接线', 'type': 'welding',
              'service_key': 'weld_2', 'enabled_setting': 'weld_enabled'}
    monkeypatch.setattr(production_lines, 'SERVICE_LINES', (*production_lines.SERVICE_LINES, second))
    context = SimpleNamespace(welding=SimpleNamespace(settings=SimpleNamespace(weld_enabled=False)),
                              weld_2=SimpleNamespace(settings=SimpleNamespace(weld_enabled=True, password='secret')))
    items = production_lines.registered_lines(context)
    assert all({'id', 'name', 'type', 'enabled', 'capabilities', 'home_path'} <= item.keys() for item in items)
    first, other = [item for item in items if item['type'] == 'welding']
    assert not first['enabled'] and other['enabled']
    assert other['id'] == 'weld-2' and other['home_path'] == '/admin/welding/home'
    assert 'secret' not in str(items) and 'service_key' not in str(items)
    assert production_lines.supports_path('weld-2', '/api/v1/welding/jobs')
    assert not production_lines.supports_path('weld-2', '/api/v1/alarms/status')


@pytest.mark.parametrize('line,path,allowed', [
    ('welding', '/api/v1/welding/jobs', True),
    ('welding', '/api/v1/cutting/dispatch', False),
    ('welding', '/api/v1/reports', False),
    ('Cutter01', '/api/v1/reports', True),
    ('Cutter01', '/api/v1/welding/jobs', False),
    ('unknown', '/api/v1/reports', False),
    ('welding', '/api/v1/reportsettings', True),
    ('welding', '/api/v1/database/configuration', True),
])
def test_capability_scope_is_registered_and_matches_whole_modules(line, path, allowed):
    assert production_lines.supports_path(line, path) is allowed


def test_welding_api_resolves_the_registered_service_and_keeps_caches_separate(monkeypatch):
    from app.api import welding
    from app.config import Settings
    from app.services import welding_service
    from fastapi import FastAPI, Request
    second = {'id': 'weld-2', 'name': '第二焊接线', 'type': 'welding',
              'service_key': 'weld_2', 'enabled_setting': 'weld_enabled'}
    definitions = (*production_lines.SERVICE_LINES, second)
    monkeypatch.setattr(production_lines, 'SERVICE_LINES', definitions)
    monkeypatch.setattr(welding, 'SERVICE_LINES', definitions)
    monkeypatch.setattr(welding_service, 'SERVICE_LINES', definitions)
    app = FastAPI()
    app.state.welding = welding_service.WeldingService(Settings(weld_enabled=True), None)
    app.state.weld_2 = welding_service.WeldingService(Settings(weld_enabled=True), None, line_id='weld-2')
    request = Request({'type': 'http', 'app': app, 'headers': [(b'x-production-line', b'weld-2')]})
    selected = welding.service(request)
    assert selected is app.state.weld_2
    assert selected.settings.device_id == 'weld-2' and selected.settings.device_name == '第二焊接线'
    assert selected._statistics is not app.state.welding._statistics


@pytest.mark.parametrize('headers', [{}, {'X-Production-Line': 'welding'}, {'X-Production-Line': 'Cutter01'}])
def test_client_scope_never_grants_authenticated_access(headers, monkeypatch):
    from app.main import app
    from fastapi.testclient import TestClient
    monkeypatch.setattr(app.state, 'workshop', SimpleNamespace(user_for_session=lambda *args, **kwargs: None), raising=False)
    path = '/api/v1/welding/health' if headers.get('X-Production-Line') != 'Cutter01' else '/api/v1/alarms/status?device_id=Cutter01'
    assert TestClient(app).get(path, headers=headers).status_code == 401
