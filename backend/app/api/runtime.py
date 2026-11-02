from fastapi import Request
from typing import cast

from app.services.application_runtime import ApplicationRuntime
from app.services.database_runtime import DatabaseServices, database_context


def application_state(request: Request) -> ApplicationRuntime:
    # Embedded apps and test fixtures may still attach services directly to state.
    return cast(ApplicationRuntime, getattr(request.app.state, 'runtime', request.app.state))


def runtime_state(request: Request) -> DatabaseServices:
    context = getattr(request.state, 'database_context', None)
    if context is None:
        context = database_context(application_state(request))
        request.state.database_context = context
    return cast(DatabaseServices, context)
