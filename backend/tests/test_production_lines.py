from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.auth import require_admin
from app.api.welding import service
from app.device_config import load_line_catalog
from app.services.application_runtime import build_runtime
from app.services.database_runtime import database_context
from app.services.production_lines import registered_lines, supports_path
from tests.line_fixtures import configuration


@pytest.mark.parametrize('line,path,allowed', [
    ('welding', '/api/v1/welding/jobs', True), ('welding', '/api/v1/cutting/dispatch', False),
    ('welding', '/api/v1/reports', False), ('Cutter01', '/api/v1/reports', True),
    ('Cutter01', '/api/v1/welding/jobs', False), ('unknown', '/api/v1/reports', False),
    ('welding', '/api/v1/reportsettings', True), ('welding', '/api/v1/database/configuration', True),
])
def test_capability_scope_matches_registered_instance_and_whole_modules(tmp_path, line, path, allowed):
    context = SimpleNamespace(catalog=load_line_catalog(configuration(tmp_path, welding=True).production_lines_json))
    assert supports_path(context, line, path) is allowed


def test_api_resolves_explicit_instances_and_public_metadata_has_no_credentials(tmp_path):
    cfg = configuration(tmp_path, welding=True)
    runtime = build_runtime(cfg, role='web')
    context = database_context(runtime)
    app = FastAPI()
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
def test_client_scope_never_grants_authenticated_access(headers, tmp_path, monkeypatch):
    from app.main import app
    runtime = build_runtime(configuration(tmp_path, welding=True), role='web')
    monkeypatch.setattr(app.state, 'runtime', runtime, raising=False)
    path = '/api/v1/welding/health' if headers.get('X-Production-Line') != 'Cutter01' else '/api/v1/alarms/status?device_id=Cutter01'
    assert TestClient(app).get(path, headers=headers).status_code == 401
