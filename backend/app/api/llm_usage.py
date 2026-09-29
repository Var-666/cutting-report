from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin


router = APIRouter(
    prefix="/api/v1/llm-usage",
    tags=["llm-usage"],
    dependencies=[Depends(require_admin)],
)


@router.get("")
def llm_usage(request: Request) -> dict:
    return {
        "success": True,
        "data": request.app.state.llm_usage.summary(),
        "request_id": request.state.request_id,
    }
