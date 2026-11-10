"""OIDC authorization-code login; identity verification never grants local roles."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import math
import secrets
from urllib.parse import quote_plus, unquote, urlencode, urlsplit
from zoneinfo import ZoneInfo

import httpx
import jwt

from app.config import Settings
from app.database.sso import SsoRequestRepository
from app.database.unit_of_work import UnitOfWork
from app.database.users import UserRepository
from app.models.sso import account_bindings, http_url
from app.services.worker_auth_service import WorkerAuthService, WorkerAuthenticationUnavailableError


class SsoError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def safe_return_to(value: str) -> str:
    decoded = unquote(value)
    if (len(value) > 4096 or not decoded.startswith('/') or decoded.startswith('//')
            or any(ord(c) <= 32 or c == '\\' for c in decoded)):
        return '/'
    path = urlsplit(decoded).path
    if path == '/login' or path.startswith('/api/'):
        return '/'
    return value


@dataclass(frozen=True)
class SsoLogin:
    url: str
    browser: str


@dataclass(frozen=True)
class SsoSession:
    token: str
    return_to: str


class SsoService:
    def __init__(self, settings: Settings, requests: SsoRequestRepository, users: UserRepository,
                 *, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.requests = requests
        self.users = users
        self.bindings = account_bindings(settings.sso_account_bindings_json)
        self.transport = transport

    def _enabled(self) -> None:
        if not self.settings.sso_enabled:
            raise SsoError('disabled')

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        response.raise_for_status()
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError('Invalid OIDC response')
        return value

    def _metadata(self, client: httpx.Client) -> dict:
        metadata = self._json(client.get(self.settings.sso_issuer.rstrip('/') + '/.well-known/openid-configuration'))
        if metadata.get('issuer') != self.settings.sso_issuer:
            raise ValueError('Issuer mismatch')
        for name in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            endpoint = metadata[name]
            if not isinstance(endpoint, str) or not http_url(endpoint):
                raise ValueError('Invalid OIDC endpoint')
            if self.settings.sso_issuer.startswith('https:') and not endpoint.startswith('https:'):
                raise ValueError('OIDC endpoint downgrade')
        if ('S256' not in metadata.get('code_challenge_methods_supported', ['S256'])
                or 'client_secret_basic' not in metadata.get('token_endpoint_auth_methods_supported', ['client_secret_basic'])
                or 'RS256' not in metadata.get('id_token_signing_alg_values_supported', ['RS256'])):
            raise ValueError('Unsupported OIDC provider')
        return metadata

    def begin(self, return_to: str, browser: str | None) -> SsoLogin:
        self._enabled()
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        if not browser or len(browser) != 43 or not browser.isascii() or not all(c.isalnum() or c in '-_' for c in browser):
            browser = secrets.token_urlsafe(32)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        try:
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                metadata = self._metadata(client)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise SsoError('unavailable') from None
        try:
            self.requests.create(state, browser, nonce=nonce, verifier=verifier, return_to=safe_return_to(return_to))
        except ValueError:
            raise SsoError('busy') from None
        parameters = urlencode(dict(response_type='code', client_id=self.settings.sso_client_id,
            redirect_uri=self.settings.sso_redirect_uri, scope='openid', state=state, nonce=nonce,
            code_challenge=challenge, code_challenge_method='S256'))
        endpoint = metadata['authorization_endpoint']
        return SsoLogin(endpoint + ('&' if '?' in endpoint else '?') + parameters, browser)

    def _claims(self, client: httpx.Client, metadata: dict, tokens: dict, nonce: str) -> dict:
        token = tokens['id_token']
        header = jwt.get_unverified_header(token)
        if header.get('alg') != 'RS256':
            raise ValueError('Unsupported ID token algorithm')
        keys = self._json(client.get(metadata['jwks_uri']))['keys']
        candidates = [key for key in keys if isinstance(key, dict) and key.get('kty') == 'RSA'
                      and key.get('use', 'sig') == 'sig' and key.get('alg', 'RS256') == 'RS256'
                      and 'verify' in key.get('key_ops', ['verify'])
                      and (header.get('kid') is None or key.get('kid') == header['kid'])]
        if len(candidates) != 1:
            raise ValueError('Signing key missing or ambiguous')
        claims = jwt.decode(token, jwt.PyJWK(candidates[0], algorithm='RS256').key, algorithms=['RS256'],
            audience=self.settings.sso_client_id, issuer=self.settings.sso_issuer, leeway=30,
            options={'require': ['iss', 'sub', 'aud', 'exp', 'iat', 'nonce']})
        if (not isinstance(claims['sub'], str) or not 1 <= len(claims['sub']) <= 255
                or not isinstance(claims['nonce'], str) or not secrets.compare_digest(claims['nonce'], nonce)):
            raise ValueError('Invalid subject or nonce')
        for name in ('exp', 'iat'):
            if type(claims[name]) not in (int, float) or not math.isfinite(claims[name]):
                raise ValueError('Invalid token timestamp')
        if ('azp' in claims or isinstance(claims['aud'], list) and len(claims['aud']) > 1):
            if claims.get('azp') != self.settings.sso_client_id:
                raise ValueError('Invalid authorized party')
        return claims

    def _account(self, users: UserRepository, subject: str) -> dict | None:
        if subject in self.bindings:
            return users.active_user(self.bindings[subject])
        # The provider's sub is the agreed HR _Identify; explicit bindings remain one-to-one.
        user = users.active_external_user(subject)
        return user if user and user['user_id'] not in self.bindings.values() else None

    def finish(self, *, state: str, browser: str, code: str, error: str,
               worker_auth: WorkerAuthService) -> SsoSession:
        self._enabled()
        if not state or len(state) > 128 or not browser or len(browser) > 128:
            raise SsoError('state')
        flow = self.requests.consume(state, browser)
        if flow is None:
            raise SsoError('state')
        if error or not code or len(code) > 4096:
            raise SsoError('denied')
        try:
            with httpx.Client(timeout=5, follow_redirects=False, trust_env=False, transport=self.transport) as client:
                metadata = self._metadata(client)
                tokens = self._json(client.post(metadata['token_endpoint'],
                    auth=httpx.BasicAuth(quote_plus(self.settings.sso_client_id), quote_plus(self.settings.sso_client_secret)),
                    data=dict(grant_type='authorization_code', code=code, redirect_uri=self.settings.sso_redirect_uri,
                              code_verifier=flow['verifier'])))
                claims = self._claims(client, metadata, tokens, flow['nonce'])
        except httpx.HTTPError:
            raise SsoError('unavailable') from None
        except (jwt.PyJWTError, ValueError, KeyError, TypeError):
            raise SsoError('token') from None
        user = self._account(self.users, claims['sub'])
        if user is None:
            raise SsoError('account')
        user_id = user['user_id']
        identity = None
        if user['auth_source'] == 'hr_database':
            try:
                identity = worker_auth.find_person(user['external_id'] or '')
                if identity is None:
                    raise SsoError('account')
            except WorkerAuthenticationUnavailableError:
                raise SsoError('unavailable') from None
        now = datetime.now(ZoneInfo(self.settings.timezone))
        # Recheck and lock the account only after external HTTP/HR operations finish.
        with UnitOfWork(self.users.database) as unit:
            current_user = self._account(unit.users, claims['sub'])
            if current_user is None or current_user['user_id'] != user_id:
                raise SsoError('account')
            if identity is not None and not unit.users.refresh_external_profile(user_id, identity, now=now.isoformat(timespec='seconds')):
                raise SsoError('account')
            token, _ = unit.sessions.create_session(user_id, now=now, hours=self.settings.auth_session_hours)
        return SsoSession(token, safe_return_to(flow['return_to']))
