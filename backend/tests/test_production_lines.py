from tests.runtime_factories import make_database_services
from tests.postgres_fixtures import state_settings
from dataclasses import replace

import pytest
from fastapi import Request
from tests.web_fixtures import embedded_app
from fastapi.testclient import TestClient

from app.api.auth import require_admin
from app.modules.welding.routes import service
from app.device_config import load_line_catalog
from app.runtime.application import build_runtime
from app.runtime.database import database_context
from app.runtime.production_lines import registered_lines, supports_path
from tests.line_fixtures import configuration


def test_public_line_homes_are_distinct_from_the_shared_home(tmp_path):
    catalog = load_line_catalog(configuration(tmp_path, welding=True).production_lines_json)
    items = registered_lines(make_database_services(catalog=catalog))
    assert {item['home_path'] for item in items if item['type'] == 'cutting'} == {'/admin/cutting/home'}
    assert {item['home_path'] for item in items if item['type'] == 'welding'} == {'/admin/welding/home'}
    assert all(item['home_path'] != '/admin/home' for item in items)


@pytest.mark.parametrize('line,path,allowed', [
    ('welding', '/api/v1/welding/jobs', True), ('welding', '/api/v1/cutting/dispatch', False),
    ('welding', '/api/v1/reports', False), ('Cutter01', '/api/v1/reports', True),
    ('Cutter01', '/api/v1/welding/jobs', False), ('unknown', '/api/v1/reports', False),
    ('welding', '/api/v1/reportsettings', True), ('welding', '/api/v1/database/configuration', True),
])
def test_capability_scope_matches_registered_instance_and_whole_modules(tmp_path, line, path, allowed):
    context = make_database_services(catalog=load_line_catalog(configuration(tmp_path, welding=True).production_lines_json))
    assert supports_path(context, line, path) is allowed


def test_api_resolves_explicit_instances_and_public_metadata_has_no_credentials(postgres_state, tmp_path):
    cfg = configuration(tmp_path, welding=True)
    runtime = build_runtime(state_settings(cfg,postgres_state), role='web')
    context = database_context(runtime)
    app = embedded_app()
    app.state.runtime = runtime
    selected = Request({'type': 'http', 'app': app, 'headers': [(b'x-production-line', b'welding')]})
    assert service(selected) is context.lines['welding'].service
    with pytest.raises(Exception) as missing:
        service(Request({'type': 'http', 'app': app, 'headers': []}))
    assert missing.value.status_code == 422
    items = registered_lines(context)
    assert {item['id'] for item in items} == {'Cutter01', 'Cutter02', 'welding'}
    assert 'private' not in str(items)
    assert all({'id', 'name', 'source_key', 'enabled', 'type', 'capabilities'} <= item.keys() for item in items)


@pytest.mark.parametrize('headers', [{}, {'X-Production-Line': 'welding'}, {'X-Production-Line': 'Cutter01'}])
def test_client_scope_never_grants_authenticated_access(postgres_state, headers, tmp_path, monkeypatch):
    from app.main import app
    runtime = build_runtime(state_settings(configuration(tmp_path, welding=True),postgres_state), role='web')
    monkeypatch.setattr(app.state, 'runtime', runtime, raising=False)
    path = '/api/v1/welding/health' if headers.get('X-Production-Line') != 'Cutter01' else '/api/v1/alarms/status?device_id=Cutter01'
    assert TestClient(app).get(path, headers=headers).status_code == 401
