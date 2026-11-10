"""OIDC login with signed tokens, real isolated state, and no external requests."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import base64
import hashlib
import json
import logging
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import jwt
import pytest

from app.api import auth
from app.database.sessions import SessionRepository
from app.database.sso import SsoRequestRepository
from app.database.users import UserRepository
from app.models.sso import AuthStatus, account_bindings
from app.services.sso_service import SsoError, SsoService, safe_return_to
from app.services.worker_auth_service import WorkerAuthenticationUnavailableError
from tests.line_fixtures import configuration

ISSUER = 'https://auth.test'
CALLBACK = 'https://report.test/api/v1/auth/sso/callback'


@pytest.fixture(scope='module')
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Provider:
    def __init__(self, key):
        self.key = key
        self.codes = {}
        self.calls = []
        self.overrides = {}
        self.failure = None
        self.signing_algorithm = 'RS256'
        self.jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), 'kid': 'test-key'}

    def callback(self, url, **overrides):
        params = parse_qs(urlsplit(url).query)
        code = f'code-{len(self.codes)}'
        self.codes[code] = (params, overrides)
        return '/api/v1/auth/sso/callback?' + urlencode({'state': params['state'][0], 'code': code})

    def request(self, request):
        self.calls.append(request)
        if self.failure:
            raise self.failure
        if request.url.path == '/.well-known/openid-configuration':
            return httpx.Response(200, json={'issuer': ISSUER, 'authorization_endpoint': ISSUER + '/authorize',
                'token_endpoint': ISSUER + '/token', 'jwks_uri': ISSUER + '/jwks.json', **self.overrides})
        if request.url.path == '/jwks.json':
            return httpx.Response(200, json={'keys': [self.jwk]})
        assert request.url.path == '/token' and request.method == 'POST'
        assert request.headers['Authorization'] == 'Basic ' + base64.b64encode(b'report-system:client-secret').decode()
        body = parse_qs(request.content.decode())
        params, overrides = self.codes.pop(body['code'][0])
        assert body['redirect_uri'] == [CALLBACK]
        assert body['grant_type'] == ['authorization_code']
        challenge = base64.urlsafe_b64encode(hashlib.sha256(body['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
        assert params['code_challenge'] == [challenge]
        now = int(datetime.now(timezone.utc).timestamp())
        claims = {'iss': ISSUER, 'sub': 'person-1', 'aud': 'report-system', 'iat': now, 'exp': now + 300,
                  'nonce': params['nonce'][0], **overrides}
        key = self.key if self.signing_algorithm == 'RS256' else 'a-different-secret-at-least-32-bytes'
        return httpx.Response(200, json={'id_token': jwt.encode(claims, key, algorithm=self.signing_algorithm,
                                                             headers={'kid': 'test-key'})})


@pytest.fixture
def sso(postgres_state, signing_key, monkeypatch):
    database, cfg, _ = postgres_state
    users = UserRepository(database)
    now = datetime.now(timezone.utc).isoformat()
    admin = users.bootstrap_admin(username='admin', display_name='管理员', password='test-password', now=now)
    worker = users.create_user(username='worker', display_name='工人', password='test-password', role='worker', now=now)
    hr = users.upsert_external_worker(external_id='9123', company_no='C0007', group_no='G0021', display_name='HR工人', now=now)
    cfg = replace(cfg, sso_enabled=True, sso_issuer=ISSUER, sso_client_id='report-system',
                  sso_client_secret='client-secret', sso_redirect_uri=CALLBACK, auth_cookie_secure=True,
                  sso_account_bindings_json=json.dumps([{'sub': 'person-1', 'user_id': worker['user_id']},
                                                       {'sub': 'admin-1', 'user_id': admin['user_id']},
                                                       {'sub': 'hr-1', 'user_id': hr['user_id']}]))
    requests = SsoRequestRepository(database)
    provider = Provider(signing_key)
    service = SsoService(cfg, requests, users, transport=httpx.MockTransport(provider.request))
    directory = SimpleNamespace(session_identity_available=lambda *_args, **_kwargs: True,
                                cached_session_identity_available=lambda *_args, **_kwargs: None)
    app = FastAPI()
    app.include_router(auth.router)
    app.state.runtime = SimpleNamespace(sso=service, users=users, sessions=SessionRepository(database),
        database_runtime=SimpleNamespace(snapshot=lambda: SimpleNamespace(settings=cfg, worker_auth=directory)))

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'sso-test'
        return await call_next(request)

    monkeypatch.setattr(auth, 'settings', cfg)
    client = TestClient(app, base_url='https://report.test', follow_redirects=False)
    return SimpleNamespace(database=database, cfg=cfg, users=users, worker=worker, admin=admin, hr=hr,
                           requests=requests, provider=provider, service=service, directory=directory, client=client)


def begin(sso, return_to='/worker/tasks?day=2026-11-09#task'):
    response = sso.client.get('/api/v1/auth/sso/login', params={'return_to': return_to})
    assert response.status_code == 302
    assert response.headers['cache-control'] == 'no-store'
    cookie = response.headers['set-cookie']
    assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
    params = parse_qs(urlsplit(response.headers['location']).query)
    assert params['scope'] == ['openid'] and params['code_challenge_method'] == ['S256']
    assert 'client-secret' not in response.headers['location'] and 'code_verifier' not in params
    return response.headers['location']


@pytest.mark.parametrize(('subject', 'account', 'target'), [
    ('person-1', 'worker', '/worker/tasks?day=2026-11-09#task'),
    ('admin-1', 'admin', '/admin/home'),
    ('hr-1', 'hr', '/worker/tasks'),
])
def test_login_preserves_existing_account_role_and_establishes_existing_session(sso, subject, account, target):
    before = sso.users.list_users()
    response = sso.client.get(sso.provider.callback(begin(sso, target), sub=subject))
    assert response.status_code == 302 and response.headers['location'] == target
    assert 'cutting_session=' in response.headers['set-cookie']
    assert response.headers['referrer-policy'] == 'no-referrer'
    status = sso.client.get('/api/v1/auth/status').json()['data']
    AuthStatus.model_validate(status)
    assert status['sso_enabled'] and status['user'] == getattr(sso, account)
    assert sso.users.list_users() == before
    sso.client.post('/api/v1/auth/logout')
    assert sso.client.get('/api/v1/auth/status').json()['data']['user'] is None


@pytest.mark.parametrize('claims', [
    {'iss': 'https://wrong.test'}, {'aud': 'other-client'}, {'exp': 1}, {'iat': 9999999999},
    {'nonce': 'wrong'}, {'sub': ''}, {'azp': 'other-client'},
    {'aud': ['report-system', 'other-client']}, {'exp': '9999999999'},
])
def test_invalid_signed_identity_cannot_create_session(sso, claims):
    callback = sso.provider.callback(begin(sso), **claims)
    response = sso.client.get(callback)
    assert response.headers['location'] == '/login?sso_error=token'
    assert 'cutting_session' not in sso.client.cookies
    assert sso.client.get(callback).headers['location'] == '/login?sso_error=state'


def test_bad_signature_and_algorithm_are_rejected(sso, signing_key):
    url = begin(sso)
    sso.provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert sso.client.get(sso.provider.callback(url)).headers['location'] == '/login?sso_error=token'
    sso.provider.signing_algorithm = 'HS256'
    assert sso.client.get(sso.provider.callback(begin(sso))).headers['location'] == '/login?sso_error=token'


def test_callback_is_browser_bound_expires_and_is_consumed_once(sso):
    callback = sso.provider.callback(begin(sso))
    other_browser = TestClient(sso.client.app, base_url='https://report.test', follow_redirects=False)
    assert other_browser.get(callback).headers['location'] == '/login?sso_error=state'
    assert sso.client.get(callback).headers['location'] == '/worker/tasks?day=2026-11-09#task'
    calls = len(sso.provider.calls)
    assert sso.client.get(callback).headers['location'] == '/login?sso_error=state'
    assert len(sso.provider.calls) == calls
    callback = sso.provider.callback(begin(sso))
    with sso.database.connect() as connection:
        connection.execute('UPDATE sso_requests SET expires_at=0')
    assert sso.client.get(callback).headers['location'] == '/login?sso_error=state'


def test_two_tabs_complete_independent_logins_and_concurrent_consumers_cannot_replay(sso):
    first, second = begin(sso, '/admin/home'), begin(sso, '/worker/tasks')
    assert sso.client.get(sso.provider.callback(first)).headers['location'] == '/admin/home'
    assert sso.client.get(sso.provider.callback(second)).headers['location'] == '/worker/tasks'
    state = parse_qs(urlsplit(begin(sso)).query)['state'][0]
    browser = sso.client.cookies.get(auth.SSO_BROWSER_COOKIE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: sso.requests.consume(state, browser), range(2)))
    assert sum(result is not None for result in results) == 1


@pytest.mark.parametrize('subject', ['unknown-person', 'person-1'])
def test_missing_or_disabled_account_is_not_created_or_reactivated(sso, subject):
    with sso.database.connect() as connection:
        connection.execute('UPDATE users SET active=0 WHERE user_id=%s', (sso.worker['user_id'],))
    before = sso.users.list_users()
    response = sso.client.get(sso.provider.callback(begin(sso), sub=subject))
    assert response.headers['location'] == '/login?sso_error=account'
    assert sso.users.list_users() == before
    assert 'cutting_session' not in sso.client.cookies


@pytest.mark.parametrize('subject', ['hr-1', '9123'])
def test_hr_availability_is_checked_before_issuing_a_session(sso, subject):
    if subject == '9123':
        sso.service.bindings = {}
    sso.directory.session_identity_available = lambda *_args, **_kwargs: False
    assert sso.client.get(sso.provider.callback(begin(sso), sub=subject)).headers['location'] == '/login?sso_error=account'

    def unavailable(*args, **kwargs):
        raise WorkerAuthenticationUnavailableError('private database details')
    sso.directory.session_identity_available = unavailable
    response = sso.client.get(sso.provider.callback(begin(sso), sub=subject))
    assert response.headers['location'] == '/login?sso_error=unavailable'
    assert 'cutting_session' not in sso.client.cookies


@pytest.mark.parametrize('role', ['worker', 'admin'])
def test_hr_subject_logs_into_existing_account_without_manual_binding(sso, role):
    sso.service.bindings = {}
    if role == 'admin':
        sso.users.grant_external_admin(identity=sso.hr, operator=sso.admin,
                                       now=datetime.now(timezone.utc).isoformat())
    before = sso.users.list_users()
    calls = []
    sso.directory.session_identity_available = lambda key, **kwargs: calls.append((key, kwargs)) or True
    response = sso.client.get(sso.provider.callback(begin(sso, '/'), sub='9123'))
    assert response.headers['location'] == '/'
    current = sso.client.get('/api/v1/auth/status').json()['data']['user']
    assert current['user_id'] == sso.hr['user_id'] and current['role'] == role
    assert calls[0] == ('9123', {'force': True})
    assert sso.users.list_users() == before


@pytest.mark.parametrize('subject', ['unknown-person', 'G0021', 'C0007', '09123', 'admin'])
def test_hr_auto_matching_requires_exact_external_id_and_never_creates_accounts(sso, subject):
    sso.service.bindings = {}
    before = sso.users.list_users()
    response = sso.client.get(sso.provider.callback(begin(sso), sub=subject,
                                                   preferred_username='G0021', name='管理员', role='admin'))
    assert response.headers['location'] == '/login?sso_error=account'
    assert sso.users.list_users() == before
    assert 'cutting_session' not in sso.client.cookies


def test_hr_auto_matching_cannot_bypass_an_explicit_binding(sso):
    response = sso.client.get(sso.provider.callback(begin(sso), sub='9123'))
    assert response.headers['location'] == '/login?sso_error=account'
    response = sso.client.get(sso.provider.callback(begin(sso), sub='hr-1'))
    assert response.headers['location'].startswith('/worker/tasks')


@pytest.mark.parametrize('during_hr_check', [False, True])
def test_hr_auto_matching_never_reactivates_disabled_accounts(sso, during_hr_check):
    sso.service.bindings = {}
    def disable(*args, **kwargs):
        with sso.database.connect() as connection:
            connection.execute('UPDATE users SET active=0 WHERE user_id=%s', (sso.hr['user_id'],))
        return True
    if during_hr_check:
        sso.directory.session_identity_available = disable
    else:
        disable()
    response = sso.client.get(sso.provider.callback(begin(sso), sub='9123'))
    assert response.headers['location'] == '/login?sso_error=account'
    assert not next(user for user in sso.users.list_users() if user['user_id'] == sso.hr['user_id'])['active']
    assert 'cutting_session' not in sso.client.cookies


def test_hr_identity_is_rechecked_after_external_verification(sso):
    sso.service.bindings = {}
    def change_identity(*args, **kwargs):
        with sso.database.connect() as connection:
            connection.execute('UPDATE users SET external_id=%s WHERE user_id=%s', ('another-person', sso.hr['user_id']))
        return True
    sso.directory.session_identity_available = change_identity
    response = sso.client.get(sso.provider.callback(begin(sso), sub='9123'))
    assert response.headers['location'] == '/login?sso_error=account'
    assert 'cutting_session' not in sso.client.cookies


def test_cancellation_and_duplicate_parameters_do_not_exchange_a_code(sso):
    params = parse_qs(urlsplit(begin(sso)).query)
    path = '/api/v1/auth/sso/callback?' + urlencode({'state': params['state'][0], 'error': 'access_denied'})
    calls = len(sso.provider.calls)
    assert sso.client.get(path).headers['location'] == '/login?sso_error=denied'
    assert len(sso.provider.calls) == calls
    callback = sso.provider.callback(begin(sso))
    assert sso.client.get(callback + '&state=another').headers['location'] == '/login?sso_error=state'


def test_provider_failure_is_sanitized_and_password_login_remains_available(sso):
    sso.provider.failure = httpx.ConnectError('private-address-or-secret')
    response = sso.client.get('/api/v1/auth/sso/login')
    assert response.headers['location'] == '/login?sso_error=unavailable'
    assert 'private' not in response.text
    response = sso.client.post('/api/v1/auth/login', json={'username': 'admin', 'password': 'test-password'})
    assert response.status_code == 200 and response.json()['data']['user']['role'] == 'admin'


def test_discovery_issuer_mismatch_and_https_downgrade_are_rejected(sso):
    for overrides in ({'issuer': 'https://evil.test'}, {'token_endpoint': 'http://auth.test/token'}):
        sso.provider.overrides = overrides
        assert sso.client.get('/api/v1/auth/sso/login').headers['location'] == '/login?sso_error=unavailable'


def test_disabled_sso_makes_no_external_requests_and_preserves_status(sso):
    sso.service.settings = replace(sso.cfg, sso_enabled=False)
    assert sso.client.get('/api/v1/auth/status').json()['data']['sso_enabled'] is False
    assert sso.client.get('/api/v1/auth/sso/login').headers['location'] == '/login?sso_error=disabled'
    assert sso.provider.calls == []


@pytest.mark.parametrize('path', ['https://evil.test', '//evil.test', '/\\evil.test', '/%2fevil.test', '/%0d%0aevil', '/login', '/api/v1/auth/sso/login'])
def test_return_target_is_a_business_path_on_this_site(path):
    assert safe_return_to(path) == '/'


def test_binding_and_configuration_validation_fail_without_echoing_secrets(tmp_path):
    assert account_bindings('[{"sub":"exact-sub","user_id":"existing-id"}]') == {'exact-sub': 'existing-id'}
    for raw in ('{}', '[{"sub":"a","user_id":"u"},{"sub":"b","user_id":"u"}]',
                '[{"sub":"a","user_id":"u"},{"sub":"a","user_id":"v"}]', '[{"sub":1,"user_id":"u"}]'):
        with pytest.raises(ValueError):
            account_bindings(raw)
    with pytest.raises(ValueError, match='SSO_ISSUER'):
        configuration(tmp_path, sso_enabled=True)
    cfg = configuration(tmp_path, sso_client_secret='private-secret')
    assert 'private-secret' not in repr(cfg)


def test_callback_credentials_are_removed_from_uvicorn_access_log():
    from app.main import _redact_sso_callback
    record = logging.LogRecord('uvicorn.access', logging.INFO, '', 0, '%s - "%s %s HTTP/%s" %d',
        ('127.0.0.1', 'GET', '/api/v1/auth/sso/callback?code=secret&state=secret', '1.1', 302), None)
    assert _redact_sso_callback(record)
    assert 'secret' not in record.getMessage()


@pytest.mark.parametrize(('claim', 'offset', 'accepted'), [('iat', 1, True), ('iat', 10, True), ('iat', 60, False),
    ('nbf', 10, True), ('nbf', 60, False), ('exp', -10, True), ('exp', -60, False)])
def test_oidc_clock_skew_is_bounded_to_thirty_seconds(sso, claim, offset, accepted):
    now = int(datetime.now(timezone.utc).timestamp())
    response = sso.client.get(sso.provider.callback(begin(sso, '/worker/tasks'), **{claim: now + offset}))
    assert response.headers['location'] == ('/worker/tasks' if accepted else '/login?sso_error=token')
    assert ('cutting_session' in sso.client.cookies) is accepted
