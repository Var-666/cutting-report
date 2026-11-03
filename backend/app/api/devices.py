from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_user
from app.config import Settings
from app.device_config import PublicLineCatalog
from app.models.contracts import ApiEnvelope
from app.api.runtime import runtime_state
from app.services.database_runtime import DatabaseServices
from app.services.device_runtime import DeviceRuntime

router = APIRouter(prefix='/api/v1/devices', tags=['devices'], dependencies=[Depends(require_user)])


def device_state(request: Request) -> DeviceRuntime:
    context = runtime_state(request)
    runtimes = context.lines
    values = request.query_params.getlist('device_id') if hasattr(request.query_params, 'getlist') else [request.query_params.get('device_id')]
    if len(values) != 1 or not values[0]:
        raise HTTPException(status_code=422, detail='必须明确指定唯一的 device_id')
    device_id = values[0]
    runtime = runtimes.get(device_id)
    if runtime is None:
        raise HTTPException(status_code=422, detail='设备不存在或尚未接入')
    from app.services.production_lines import historical_path
    if not runtime.config.enabled and not (request.method == 'GET' and historical_path(request.url.path)):
        raise HTTPException(status_code=503, detail='产线已停用')
    selected = request.headers.get('X-Production-Line')
    if selected and selected != device_id:
        raise HTTPException(status_code=403, detail='产线上下文与 device_id 不一致')
    try:
        return runtime.cutting()
    except ValueError:
        raise HTTPException(status_code=403, detail='当前产线不支持切割业务') from None


def device_settings(request: Request) -> Settings:
    return device_state(request).settings


@router.get('', response_model=ApiEnvelope[PublicLineCatalog])
def list_devices(request: Request, context: DatabaseServices = Depends(runtime_state)) -> dict[str, Any]:
    from app.services.production_lines import registered_lines
    items = [item for item in registered_lines(context) if item['type'] == 'cutting' and item['enabled']]
    return {'success': True, 'data': {'default_device_id': context.catalog.default_line_id or '', 'items': items},
            'request_id': request.state.request_id}
