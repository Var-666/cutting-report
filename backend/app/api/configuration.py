from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_admin


router = APIRouter(prefix="/api/v1/configuration", tags=["configuration"], dependencies=[Depends(require_admin)])


@router.get("/diagnostics")
def configuration_diagnostics(request: Request) -> dict:
    try:
        worker = request.app.state.worker_status_reader.status()
        data = request.app.state.configuration_diagnostics.report(worker)
    except Exception:
        # Do not expose parser exceptions, paths, raw inputs or credentials.
        raise HTTPException(status_code=503, detail="配置诊断暂不可用，请稍后重试") from None
    return {"success": True, "data": data, "request_id": request.state.request_id}
