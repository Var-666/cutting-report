from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.runtime import runtime_state
from app.api.auth import require_admin
from app.services.file_download_service import DownloadError
from app.models.conditions import ConditionSearch
from app.services.condition_service import ConditionError


router = APIRouter(prefix="/api/v1/cutting/downloads", tags=["cutting-downloads"], dependencies=[Depends(require_admin)])


class DownloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=128)
    version_id: str | None = Field(max_length=128)
    file_path: str = Field(min_length=1, max_length=2048)


def _envelope(request, data):
    return {"success": True, "data": data, "request_id": request.state.request_id}


def _call(operation):
    try:
        return operation()
    except DownloadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from None


@router.post("", status_code=201)
def download_file(payload: DownloadRequest, request: Request, response: Response, user: dict = Depends(require_admin)):
    response.headers["Cache-Control"] = "no-store"
    item = _call(lambda: runtime_state(request).file_downloads.download(**payload.model_dump(), user=user))
    return _envelope(request, item)


@router.get("")
def list_downloads(request: Request, response: Response, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100)):
    response.headers["Cache-Control"] = "no-store"
    return _envelope(request, runtime_state(request).file_downloads.list(page, page_size))


@router.post("/search")
def search_downloads(payload: ConditionSearch, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        data = runtime_state(request).file_downloads.list(payload.page, payload.page_size, payload.filters, sort=payload.sort)
    except ConditionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _envelope(request, data)
