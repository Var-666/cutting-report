from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_user
from app.services.request_cancellation import sql_request_cancellations


router = APIRouter(prefix="/api/v1/requests", tags=["requests"])
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")


@router.post("/{request_id}/cancel")
def cancel_request(
    request: Request,
    request_id: str,
    _user: dict = Depends(require_user),
) -> dict:
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise HTTPException(status_code=400, detail="请求标识无效")
    # Always return accepted so the endpoint does not disclose whether another
    # request is currently running.
    sql_request_cancellations.cancel(request_id)
    return {
        "success": True,
        "data": {"accepted": True},
        "request_id": request.state.request_id,
    }
