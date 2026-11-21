from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings
from app.services.application_runtime import ApplicationRuntime
from app.services.database_runtime import DatabaseServices, database_context


def application_state(request: Request) -> ApplicationRuntime:
    try:
        return request.app.state.runtime
    except AttributeError:
        raise RuntimeError("应用运行时未装配，请在启动时设置 app.state.runtime") from None


def runtime_state(request: Request) -> DatabaseServices:
    context: DatabaseServices | None = getattr(request.state, 'database_context', None)
    if context is None:
        state = application_state(request)
        diagnostics_path = '/api/v1/configuration/diagnostics'
        mounted_path = request.scope.get('root_path', '').rstrip('/') + diagnostics_path
        if request.scope.get('path') in (diagnostics_path, mounted_path):
            context = state.database_runtime.current_context
        else:
            context = database_context(state)
        request.state.database_context = context
    return context


def request_settings(request: Request) -> Settings:
    """Return the Settings object pinned to this request's database snapshot."""
    return runtime_state(request).settings


CurrentSettings = Annotated[Settings, Depends(request_settings)]


def request_automation_settings(request: Request) -> Settings:
    """Pin the separate automation and SMTP snapshot on first use."""
    settings = getattr(request.state, 'automation_settings', None)
    if settings is None:
        settings, _ = application_state(request).automation_runtime.snapshot()
        request.state.automation_settings = settings
    return settings
