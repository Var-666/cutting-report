from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_user
from app.config import DEVICE_CATALOG, Settings, settings

router = APIRouter(prefix='/api/v1/devices', tags=['devices'], dependencies=[Depends(require_user)])


def device_state(request: Request) -> Any:
    runtimes = getattr(request.app.state, 'devices', None)
    device_id = getattr(request, 'query_params', {}).get('device_id', DEVICE_CATALOG.default_device_id)
    if runtimes is None:
        # Small API test apps and integrations may still bind a single runtime.
        if device_id != DEVICE_CATALOG.default_device_id:
            raise HTTPException(status_code=422, detail='设备不存在或尚未接入')
        return request.app.state
    runtime = runtimes.get(device_id)
    if runtime is None:
        raise HTTPException(status_code=422, detail='设备不存在或尚未接入')
    return runtime


def device_settings(request: Request, fallback: Settings | None = None) -> Settings:
    return getattr(device_state(request), 'settings', fallback or settings)


@router.get('')
def list_devices(request: Request) -> dict[str, Any]:
    runtimes = getattr(request.app.state, 'devices', {})
    items = []
    for item in DEVICE_CATALOG.devices:
        cfg = runtimes[item.id].settings if item.id in runtimes else None
        items.append({'id': item.id, 'name': item.name, 'production_line_id': item.production_line_id,
                      'machine_no': cfg.report_machine_no if cfg else item.machine_no,
                      'energy_connected': item.energy is not None,
                      'alarm_start_at': cfg.alarm_monitor_start_at if cfg else item.alarm.start_at})
    return {'success': True, 'data': {'default_device_id': DEVICE_CATALOG.default_device_id, 'items': items},
            'request_id': request.state.request_id}
