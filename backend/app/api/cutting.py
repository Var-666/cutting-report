from __future__ import annotations

import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from app.api.auth import require_admin
from app.models.conditions import ConditionSearch
from app.services.condition_service import ConditionError
from app.services.cutting_dispatch_service import (
    CuttingDispatchError,
    CuttingFileNotFoundError,
    DispatchConflictError,
    DispatchNotFoundError,
    DuplicateDispatchError,
)
from app.services.magic_box_service import MagicBoxError


router = APIRouter(
    prefix="/api/v1/cutting",
    tags=["cutting-dispatch"],
    dependencies=[Depends(require_admin)],
)


def _envelope(request: Request, data: object) -> dict:
    return {"success": True, "data": data, "request_id": request.state.request_id}


class DispatchCreateRequest(BaseModel):
    document_id: str | None = Field(default=None, min_length=1, max_length=128)
    download_id: str | None = Field(default=None, pattern=r"^dl_[a-f0-9]{32}$")
    machine_ip: str = Field(min_length=3, max_length=64)
    material: str | None = Field(default=None, max_length=80)
    thickness: float | None = Field(default=None, ge=0, le=1000)
    quantity: int = Field(default=1, ge=1, le=10000)

    @field_validator("document_id", "machine_ip")
    @classmethod
    def normalize_required(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or any(ord(char) < 32 for char in cleaned):
            raise ValueError("不能为空或包含控制字符")
        return cleaned

    @model_validator(mode="after")
    def require_one_file_reference(self):
        if bool(self.document_id) == bool(self.download_id):
            raise ValueError("document_id和download_id必须且只能填写一个")
        return self

    @field_validator("machine_ip")
    @classmethod
    def validate_machine_ip(cls, value: str) -> str:
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            raise ValueError("目标机床IP格式不正确") from exc
        return value

    @field_validator("material")
    @classmethod
    def normalize_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


@router.get("/status")
def cutting_status(request: Request) -> dict:
    return _envelope(request, request.app.state.cutting_dispatch.status())


@router.get("/machines")
def list_machines(request: Request) -> dict:
    try:
        data = request.app.state.cutting_dispatch.list_machines()
    except MagicBoxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _envelope(request, data)


@router.get("/projects")
def list_projects(
    request: Request,
    keyword: str = Query(default="", max_length=128),
    limit: int = Query(default=100, ge=1, le=100),
) -> dict:
    items = request.app.state.cutting_dispatch.list_projects(keyword, limit)
    return _envelope(request, {"items": items})


@router.get("/catalogs")
def list_catalogs(
    request: Request,
    project_id: str = Query(min_length=1, max_length=128),
) -> dict:
    data = request.app.state.cutting_dispatch.list_catalogs(project_id)
    return _envelope(request, data)


@router.get("/files")
def list_files(
    request: Request,
    project_id: str = Query(min_length=1, max_length=128),
    catalog_id: str | None = Query(default=None, max_length=128),
    status: str = Query(default="all", pattern=r"^(all|pending|sent|failed)$"),
    keyword: str = Query(default="", max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict:
    data = request.app.state.cutting_dispatch.list_files(
        project_id,
        catalog_id=catalog_id,
        status=status,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )
    return _envelope(request, data)


@router.post("/dispatches", status_code=201)
def create_dispatch(
    payload: DispatchCreateRequest,
    request: Request,
    user: dict = Depends(require_admin),
) -> dict:
    try:
        item = request.app.state.cutting_dispatch.create_dispatch(
            **payload.model_dump(), user=user
        )
    except CuttingFileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateDispatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (CuttingDispatchError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _envelope(request, item)


@router.post("/files/search")
def search_files(payload: ConditionSearch, request: Request):
    try:
        data = request.app.state.cutting_dispatch.search_files(payload.filters, page=payload.page, page_size=payload.page_size, sort=payload.sort)
    except ConditionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _envelope(request, data)


@router.get("/dispatches")
def list_dispatches(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
) -> dict:
    return _envelope(
        request,
        request.app.state.cutting_dispatch.list_dispatches(
            page=page, page_size=page_size
        ),
    )


@router.post("/dispatches/{dispatch_id}/execute")
def execute_dispatch(
    dispatch_id: str,
    request: Request,
    user: dict = Depends(require_admin),
) -> dict:
    try:
        item = request.app.state.cutting_dispatch.execute_dispatch(
            dispatch_id, user=user
        )
    except DispatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DispatchConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MagicBoxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except CuttingDispatchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _envelope(request, item)


@router.post("/dispatches/{dispatch_id}/cancel")
def cancel_dispatch(dispatch_id: str, request: Request) -> dict:
    try:
        item = request.app.state.cutting_dispatch.cancel_dispatch(dispatch_id)
    except DispatchNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DispatchConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except MagicBoxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return _envelope(request, item)
