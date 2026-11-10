"""SSO configuration values and the public authentication status contract."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class SsoAccountBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sub: str = Field(min_length=1, max_length=255)
    user_id: str = Field(min_length=1, max_length=128)


def account_bindings(value: str) -> dict[str, str]:
    try:
        items = TypeAdapter(list[SsoAccountBinding]).validate_json(value)
    except ValidationError:
        raise ValueError('SSO_ACCOUNT_BINDINGS_JSON 必须是包含 sub、user_id 的对象数组') from None
    if len({item.sub for item in items}) != len(items) or len({item.user_id for item in items}) != len(items):
        raise ValueError('SSO_ACCOUNT_BINDINGS_JSON 的 sub 和 user_id 必须一对一且不能重复')
    return {item.sub: item.user_id for item in items}


def http_url(value: str, *, issuer: bool = False) -> bool:
    try:
        url = urlsplit(value)
        return bool(url.scheme in {'http', 'https'} and url.hostname and url.port != 0
                    and not url.username and not url.password and not url.fragment
                    and not any(ord(c) <= 32 or c == '\\' for c in value)
                    and (not issuer or not url.query))
    except ValueError:
        return False


class AuthUser(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: Literal['admin', 'worker']
    active: bool
    auth_source: str
    external_id: str | None
    group_no: str | None
    company_no: str | None
    created_at: str


class AuthStatus(BaseModel):
    setup_required: bool
    worker_database_auth_enabled: bool
    sso_enabled: bool
    authenticated: bool
    user: AuthUser | None
