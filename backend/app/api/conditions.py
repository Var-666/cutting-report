from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_admin
from app.config import settings
from app.services.condition_service import ConditionError, schema_for


router = APIRouter(prefix="/api/v1/conditions", tags=["conditions"], dependencies=[Depends(require_admin)])


@router.get("/{dataset}/schema")
def condition_schema(dataset: str, request: Request):
    try:
        data = schema_for(dataset, settings)
    except ConditionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"success": True, "data": data, "request_id": request.state.request_id}
