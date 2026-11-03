from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_admin
from app.api.runtime import runtime_state
from app.services.production_lines import registered_lines
from app.device_config import PublicLineCatalog, ProductionLineCatalog
from app.models.contracts import ApiEnvelope
from app.api.runtime import application_state
from app.services.database_runtime import DatabaseServices


router = APIRouter(prefix='/api/v1/production-lines', tags=['production-lines'], dependencies=[Depends(require_admin)])


@router.get('', response_model=ApiEnvelope[PublicLineCatalog])
def production_lines(request: Request, context: DatabaseServices = Depends(runtime_state)):
    return {'success': True, 'data': {'default_device_id': context.catalog.default_line_id or '',
                                    'items': registered_lines(context)},
            'request_id': getattr(request.state, 'request_id', '')}


@router.put('', response_model=ApiEnvelope[PublicLineCatalog])
def save_production_lines(payload: ProductionLineCatalog, request: Request):
    try:
        runtime = application_state(request).database_runtime
        runtime.save_lines(payload)
    except OSError:
        raise HTTPException(status_code=500, detail='产线配置保存失败，运行快照未改变') from None
    except ValueError:
        raise HTTPException(status_code=422, detail="产线目录或绑定无效，请核对稳定 ID、设备范围、引擎及来源启用状态") from None
    context = runtime.current_context
    return {'success': True, 'data': {'default_device_id': context.catalog.default_line_id or '',
                                    'items': registered_lines(context)},
            'request_id': getattr(request.state, 'request_id', '')}
