from typing import Annotated, cast

from fastapi import Depends, Request

from app.config import Settings
from app.services.application_runtime import ApplicationRuntime
from app.services.database_runtime import DatabaseServices, database_context


def application_state(request: Request) -> ApplicationRuntime:
    # Embedded apps and test fixtures may still attach services directly to state.
    return cast(ApplicationRuntime, getattr(request.app.state, 'runtime', request.app.state))


def runtime_state(request: Request) -> DatabaseServices:
    context = getattr(request.state, 'database_context', None)
    if context is None:
        state = application_state(request)
        database = getattr(state, 'database_runtime', None)
        scope = getattr(request, 'scope', {})
        diagnostics_path = '/api/v1/configuration/diagnostics'
        mounted_path = scope.get('root_path', '').rstrip('/') + diagnostics_path
        if database is not None and scope.get('path') in (diagnostics_path, mounted_path):
            context = database.current_context
        else:
            context = database_context(state)
        request.state.database_context = context
    return cast(DatabaseServices, context)


def request_settings(request: Request) -> Settings:
    """Return the Settings object pinned to this request's database snapshot."""
    context = runtime_state(request)
    try:
        settings = context.settings
    except AttributeError:
        raise RuntimeError("请求运行时未装配配置快照") from None
    if settings is None:
        raise RuntimeError("请求运行时未装配配置快照")
    return settings


CurrentSettings = Annotated[Settings, Depends(request_settings)]


def request_automation_settings(request: Request) -> Settings:
    """Pin the separate automation and SMTP snapshot on first use."""
    settings = getattr(request.state, 'automation_settings', None)
    if settings is None:
        settings, _ = application_state(request).automation_runtime.snapshot()
        request.state.automation_settings = settings
    return settings
