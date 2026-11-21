import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.auth import router
from tests.line_fixtures import configuration
from tests.web_fixtures import embedded_app
from tests.runtime_factories import install_runtime, make_application_runtime, make_database_services


APP = Path(__file__).resolve().parents[1] / 'app'


def test_application_does_not_read_global_settings():
    violations = []
    for path in APP.rglob('*.py'):
        tree = ast.parse(path.read_text(), filename=str(path))
        root_aliases, config_aliases = {'app'}, set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name == 'app':
                        root_aliases.add(item.asname or 'app')
                    if item.name == 'app.config' and item.asname:
                        config_aliases.add(item.asname)
            if isinstance(node, ast.ImportFrom) and node.module == 'app':
                config_aliases.update(item.asname or item.name for item in node.names if item.name == 'config')

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'app.config':
                if any(item.name in {'settings', '*'} for item in node.names):
                    violations.append(f'{path}:{node.lineno} imports configuration singleton')
            if isinstance(node, ast.Attribute) and node.attr == 'settings':
                owner = ast.unparse(node.value)
                if owner in config_aliases | {f'{alias}.config' for alias in root_aliases}:
                    violations.append(f'{path}:{node.lineno} reads configuration singleton')
            if path.parent == APP / 'api' and isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == 'getattr' and len(node.args) >= 2:
                    key = node.args[1]
                    if isinstance(key, ast.Constant) and key.value == 'settings':
                        violations.append(f'{path}:{node.lineno} probes for Settings instead of using request_settings')

        constructors = {'Settings', *(f'{alias}.Settings' for alias in config_aliases),
                        *(f'{alias}.config.Settings' for alias in root_aliases)}
        constructors.update(item.asname for node in tree.body
                            if isinstance(node, ast.ImportFrom) and node.module == 'app.config'
                            for item in node.names if item.name == 'Settings' and item.asname)
        for statement in tree.body:
            if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
                for call in ast.walk(statement):
                    if isinstance(call, ast.Call) and ast.unparse(call.func) in constructors:
                        violations.append(f'{path}:{call.lineno} constructs Settings at module import')

    config = ast.parse((APP / 'config.py').read_text())
    assert not any(isinstance(node, ast.Name) and node.id == 'settings' and isinstance(node.ctx, ast.Store)
                   for statement in config.body if isinstance(statement, (ast.Assign, ast.AnnAssign))
                   for node in ast.walk(statement))
    assert not violations, '\n'.join(violations)


def test_login_uses_same_snapshot_for_hr_and_session_settings(tmp_path):
    settings = configuration(tmp_path, worker_database_auth_enabled=True, auth_session_hours=7, auth_cookie_secure=True)
    updated = replace(settings, worker_database_auth_enabled=False, auth_session_hours=2, auth_cookie_secure=False)
    identity = {'user_id': 'hr-worker', 'role': 'worker'}
    hr_calls, session_hours = [], []

    def authenticate_hr(username, password):
        hr_calls.append((username, password))
        return identity

    current = make_database_services(settings=settings, worker_auth=SimpleNamespace(authenticate=authenticate_hr))
    original = current

    def authenticate_local(*_args):
        nonlocal current
        current = replace_context
        return {'user_id': 'local-worker', 'role': 'worker'}

    def create_session(user_id, *, now, hours):
        assert user_id == 'hr-worker'
        session_hours.append(hours)
        return 'session', 'expires'

    replace_context = make_database_services(settings=updated)
    app = embedded_app(settings)
    install_runtime(app, make_application_runtime(
        database_runtime=SimpleNamespace(snapshot=lambda: current),
        users=SimpleNamespace(authenticate=authenticate_local, upsert_external_worker=lambda **_: identity),
        sessions=SimpleNamespace(create_session=create_session),
    ))

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'settings-boundary'
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        response = client.post('/api/v1/auth/login', json={'username': 'worker', 'password': 'test'})
    assert response.status_code == 200 and response.json()['data']['user'] == identity
    assert current is not original and hr_calls == [('worker', 'test')]
    assert session_hours == [7]
    assert 'Max-Age=25200' in response.headers['set-cookie'] and 'Secure' in response.headers['set-cookie']
