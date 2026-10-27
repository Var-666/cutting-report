from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.api.runtime import runtime_state
from app.config import DEVICE_CATALOG
from app.services.production_lines import registered_lines


router = APIRouter(prefix='/api/v1/production-lines', tags=['production-lines'], dependencies=[Depends(require_admin)])


@router.get('')
def production_lines(request: Request):
    return {'success': True, 'data': {'default_device_id': DEVICE_CATALOG.default_device_id,
                                    'items': registered_lines(runtime_state(request))},
            'request_id': getattr(request.state, 'request_id', '')}
