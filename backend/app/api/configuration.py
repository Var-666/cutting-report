from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.runtime import application_state
from app.api.auth import require_admin
from app.models.contracts import ApiEnvelope
from app.models.configuration_contracts import ConfigurationDiagnostics


router = APIRouter(prefix="/api/v1/configuration", tags=["configuration"], dependencies=[Depends(require_admin)])


@router.get("/diagnostics", response_model=None, responses={200: {"model": ApiEnvelope[ConfigurationDiagnostics]}})
def configuration_diagnostics(request: Request) -> dict:
    try:
        worker = application_state(request).worker_status_reader.status()
        data = application_state(request).configuration_diagnostics.report(worker)
    except Exception:
        # Do not expose parser exceptions, paths, raw inputs or credentials.
        raise HTTPException(status_code=503, detail="配置诊断暂不可用，请稍后重试") from None
    return {"success": True, "data": data, "request_id": request.state.request_id}
