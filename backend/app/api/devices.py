from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_user
from app.config import DEVICE_CATALOG, Settings
from app.api.runtime import runtime_state

router = APIRouter(prefix='/api/v1/devices', tags=['devices'], dependencies=[Depends(require_user)])


def device_state(request: Request) -> Any:
    runtimes = getattr(runtime_state(request), 'devices', None)
    values = request.query_params.getlist('device_id') if hasattr(request.query_params, 'getlist') else [request.query_params.get('device_id')]
    if len(values) != 1 or not values[0]:
        raise HTTPException(status_code=422, detail='必须明确指定唯一的 device_id')
    device_id = values[0]
    if runtimes is None:
        raise HTTPException(status_code=503, detail='设备上下文尚未初始化')
    runtime = runtimes.get(device_id)
    if runtime is None:
        raise HTTPException(status_code=422, detail='设备不存在或尚未接入')
    return runtime


def device_settings(request: Request) -> Settings:
    return device_state(request).settings


@router.get('')
def list_devices(request: Request) -> dict[str, Any]:
    runtimes = getattr(runtime_state(request), 'devices', {})
    items = []
    for item in DEVICE_CATALOG.devices:
        cfg = runtimes[item.id].settings if item.id in runtimes else None
        items.append({'id': item.id, 'name': item.name, 'production_line_id': item.production_line_id,
                      'machine_no': cfg.report_machine_no if cfg else item.machine_no,
                      'energy_connected': item.energy is not None,
                      'alarm_start_at': cfg.alarm_monitor_start_at if cfg else item.alarm.start_at})
    return {'success': True, 'data': {'default_device_id': DEVICE_CATALOG.default_device_id, 'items': items},
            'request_id': request.state.request_id}
