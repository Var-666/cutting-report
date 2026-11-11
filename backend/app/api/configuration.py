from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.runtime import application_state, runtime_state
from app.api.auth import require_admin
from app.models.contracts import ApiEnvelope
from app.models.configuration_contracts import (
    ConfigurationDiagnostics, LlmCatalogRequest, LlmConfiguration, LlmConfigurationRequest, LlmModelCatalog,
)
from app.services.llm_configuration import LlmConfigurationService, ModelCatalogError
from app.services.runtime_settings_store import RuntimeSettingsSaveError


router = APIRouter(prefix="/api/v1/configuration", tags=["configuration"], dependencies=[Depends(require_admin)])


def llm_configuration_service(request: Request) -> LlmConfigurationService:
    return LlmConfigurationService(runtime_state(request).settings)


@router.get("/llm", response_model=ApiEnvelope[LlmConfiguration])
def get_llm_configuration(request: Request, service: LlmConfigurationService = Depends(llm_configuration_service)) -> dict:
    try:
        data = service.configuration()
    except RuntimeSettingsSaveError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.put("/llm", response_model=ApiEnvelope[LlmConfiguration])
def save_llm_configuration(payload: LlmConfigurationRequest, request: Request,
                           service: LlmConfigurationService = Depends(llm_configuration_service)) -> dict:
    try:
        data = service.save(payload)
    except RuntimeSettingsSaveError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.post("/llm/models", response_model=ApiEnvelope[LlmModelCatalog])
def list_llm_models(payload: LlmCatalogRequest, request: Request,
                    service: LlmConfigurationService = Depends(llm_configuration_service)) -> dict:
    try:
        data = service.models(payload)
    except (RuntimeSettingsSaveError, ModelCatalogError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"success": True, "data": data, "request_id": request.state.request_id}


@router.get("/diagnostics", response_model=None, responses={200: {"model": ApiEnvelope[ConfigurationDiagnostics]}})
def configuration_diagnostics(request: Request) -> dict:
    try:
        worker = application_state(request).worker_status_reader.status()
        data = application_state(request).configuration_diagnostics.report(worker)
    except Exception:
        # Do not expose parser exceptions, paths, raw inputs or credentials.
        raise HTTPException(status_code=503, detail="配置诊断暂不可用，请稍后重试") from None
    return {"success": True, "data": data, "request_id": request.state.request_id}
