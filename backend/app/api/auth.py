from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.database.workshop import AuthenticationError


SESSION_COOKIE = "cutting_session"
router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class UserCreateRequest(LoginRequest):
    display_name: str = Field(min_length=1, max_length=64)
    role: str = Field(default="worker", pattern=r"^(admin|worker)$")

    @field_validator("username", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


class BootstrapRequest(LoginRequest):
    display_name: str = Field(min_length=1, max_length=64)


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
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以执行此操作")
    return user


def require_worker(user: dict = Depends(require_user)) -> dict:
    if user["role"] != "worker":
        raise HTTPException(status_code=403, detail="只有工人账号可以确认任务")
    return user


@router.get("/status")
def auth_status(request: Request, user: dict | None = Depends(optional_user)) -> dict:
    return _envelope(
        request,
        {
            "setup_required": request.app.state.workshop.user_count() == 0,
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
    try:
        user = request.app.state.workshop.authenticate(
            payload.username, payload.password
        )
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    now = _now()
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
