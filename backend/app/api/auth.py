from __future__ import annotations

import secrets
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.config import settings
from app.database.workshop import AuthenticationError
from app.services.worker_auth_service import WorkerAuthenticationUnavailableError


SESSION_COOKIE = "cutting_session"
SESSION_EXPIRED_HEADER = {"X-Session-Expired": "1"}
router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("账号不能为空")
        return cleaned


class LocalAccountRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class WorkerLoginRequest(BaseModel):
    company_no: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("company_no")
    @classmethod
    def strip_company_no(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("公司工号不能为空")
        return cleaned


class UserCreateRequest(LocalAccountRequest):
    display_name: str = Field(min_length=1, max_length=64)
    role: str = Field(default="worker", pattern=r"^(admin|worker)$")

    @field_validator("username", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


class BootstrapRequest(LocalAccountRequest):
    display_name: str = Field(min_length=1, max_length=64)


class AdminGrantRequest(BaseModel):
    external_id: str = Field(min_length=1, max_length=128)
    current_password: SecretStr = Field(min_length=1, max_length=128)


class AdminRevokeRequest(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def _envelope(request: Request, data: object) -> dict:
    return {
        "success": True,
        "data": data,
        "request_id": request.state.request_id,
    }


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.auth_session_hours * 3600,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )


def optional_user(
    request: Request,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict | None:
    return request.app.state.workshop.user_for_session(
        session_token, now=_now().isoformat(timespec="seconds")
    )


def require_user(user: dict | None = Depends(optional_user)) -> dict:
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="请先登录",
            headers=SESSION_EXPIRED_HEADER,
        )
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以执行此操作")
    return user


def require_worker(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="只有工人账号可以确认任务")
    return user


def _verify_current_admin_password(
    request: Request,
    admin: dict,
    password: SecretStr,
) -> None:
    plaintext = password.get_secret_value()
    if admin.get("auth_source") == "hr_database":
        try:
            identity = request.app.state.worker_auth.authenticate(
                admin.get("company_no") or admin.get("username") or "",
                plaintext,
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="当前管理员密码错误") from exc
        except WorkerAuthenticationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not secrets.compare_digest(
            str(identity.get("external_id") or ""),
            str(admin.get("external_id") or ""),
        ):
            raise HTTPException(status_code=401, detail="当前管理员密码错误")
        return

    if not request.app.state.workshop.verify_local_user_password(
        admin["user_id"], plaintext
    ):
        raise HTTPException(status_code=401, detail="当前管理员密码错误")


@router.get("/status")
def auth_status(request: Request, user: dict | None = Depends(optional_user)) -> dict:
    return _envelope(
        request,
        {
            "setup_required": request.app.state.workshop.admin_count() == 0,
            "worker_database_auth_enabled": settings.worker_database_auth_enabled,
            "authenticated": user is not None,
            "user": user,
        },
    )


@router.post("/bootstrap")
def bootstrap(payload: BootstrapRequest, request: Request, response: Response) -> dict:
    now = _now()
    try:
        user = request.app.state.workshop.bootstrap_admin(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            now=now.isoformat(timespec="seconds"),
        )
    except (AuthenticationError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    token, expires_at = request.app.state.workshop.create_session(
        user["user_id"], now=now, hours=settings.auth_session_hours
    )
    _set_session_cookie(response, token)
    return _envelope(request, {"user": user, "expires_at": expires_at})


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    user = None
    try:
        local_user = request.app.state.workshop.authenticate(
            payload.username, payload.password
        )
        if not settings.worker_database_auth_enabled or local_user["role"] == "admin":
            user = local_user
    except AuthenticationError:
        pass

    if user is None and settings.worker_database_auth_enabled:
        try:
            identity = request.app.state.worker_auth.authenticate(
                payload.username, payload.password
            )
        except AuthenticationError as exc:
            raise HTTPException(status_code=401, detail="账号或密码错误") from exc
        except WorkerAuthenticationUnavailableError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        user = request.app.state.workshop.upsert_external_worker(
            **identity,
            now=_now().isoformat(timespec="seconds"),
        )

    if user is None:
        raise HTTPException(status_code=401, detail="账号或密码错误")

    now = _now()
    token, expires_at = request.app.state.workshop.create_session(
        user["user_id"], now=now, hours=settings.auth_session_hours
    )
    _set_session_cookie(response, token)
    return _envelope(request, {"user": user, "expires_at": expires_at})


@router.post("/hr-login")
@router.post("/worker-login", include_in_schema=False)
def worker_login(
    payload: WorkerLoginRequest,
    request: Request,
    response: Response,
) -> dict:
    try:
        identity = request.app.state.worker_auth.authenticate(
            payload.company_no, payload.password
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except WorkerAuthenticationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    now = _now()
    user = request.app.state.workshop.upsert_external_worker(
        **identity,
        now=now.isoformat(timespec="seconds"),
    )
    token, expires_at = request.app.state.workshop.create_session(
        user["user_id"], now=now, hours=settings.auth_session_hours
    )
    _set_session_cookie(response, token)
    return _envelope(request, {"user": user, "expires_at": expires_at})


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict:
    request.app.state.workshop.delete_session(session_token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return _envelope(request, {"logged_out": True})


@router.get("/users")
def list_users(
    request: Request, _: dict = Depends(require_admin)
) -> dict:
    return _envelope(request, {"items": request.app.state.workshop.list_users()})


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreateRequest,
    request: Request,
    _: dict = Depends(require_admin),
) -> dict:
    if settings.worker_database_auth_enabled:
        raise HTTPException(status_code=409, detail="账号与管理员权限由HR数据库统一管理")
    try:
        user = request.app.state.workshop.create_user(
            username=payload.username,
            display_name=payload.display_name,
            password=payload.password,
            role=payload.role,
            now=_now().isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, user)


@router.get("/hr-directory")
def search_hr_directory(
    request: Request,
    query: str = Query(min_length=1, max_length=64),
    _: dict = Depends(require_admin),
) -> dict:
    try:
        people = request.app.state.worker_auth.search_people(query)
    except WorkerAuthenticationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _envelope(request, {"items": people})


@router.get("/admins")
def list_admins(request: Request, _: dict = Depends(require_admin)) -> dict:
    return _envelope(request, {"items": request.app.state.workshop.list_admins()})


@router.post("/admins", status_code=201)
def grant_admin(
    payload: AdminGrantRequest,
    request: Request,
    admin: dict = Depends(require_admin),
) -> dict:
    _verify_current_admin_password(request, admin, payload.current_password)
    try:
        identity = request.app.state.worker_auth.find_person(payload.external_id)
    except WorkerAuthenticationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if identity is None:
        raise HTTPException(status_code=404, detail="HR人员不存在或账号不可用")
    try:
        user = request.app.state.workshop.grant_external_admin(
            identity=identity,
            operator=admin,
            now=_now().isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, user)


@router.post("/admins/{user_id}/revoke")
def revoke_admin(
    user_id: str,
    payload: AdminRevokeRequest,
    request: Request,
    admin: dict = Depends(require_admin),
) -> dict:
    _verify_current_admin_password(request, admin, payload.current_password)
    try:
        user = request.app.state.workshop.revoke_external_admin(
            user_id,
            operator=admin,
            now=_now().isoformat(timespec="seconds"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _envelope(request, user)
