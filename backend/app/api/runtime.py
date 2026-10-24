from fastapi import Request
from typing import Any

from app.services.database_runtime import database_context


def runtime_state(request: Request) -> Any:
    context = getattr(request.state, 'database_context', None)
    if context is None:
        context = database_context(request.app.state)
        request.state.database_context = context
    return context
