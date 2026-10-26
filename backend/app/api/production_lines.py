from fastapi import APIRouter, Depends, Request

from app.api.auth import require_admin
from app.api.runtime import runtime_state
from app.config import DEVICE_CATALOG


router = APIRouter(prefix='/api/v1/production-lines', tags=['production-lines'], dependencies=[Depends(require_admin)])


@router.get('')
def production_lines(request: Request):
    items = [{'id': device.id, 'name': device.name, 'machine_no': device.machine_no,
              'production_line_id': device.production_line_id, 'energy_connected': device.energy is not None,
              'alarm_start_at': device.alarm.start_at, 'type': 'cutting', 'type_name': '切割',
              'capabilities': ['reports', 'alarms', 'dispatch', 'settings']} for device in DEVICE_CATALOG.devices]
    welding = getattr(runtime_state(request), 'welding', None)
    items.append({'id': 'welding', 'name': '小组立焊接线', 'type': 'welding', 'type_name': '焊接',
                  'enabled': bool(welding and welding.settings.weld_enabled),
                  'capabilities': ['reports', 'analysis', 'settings']})
    return {'success': True, 'data': {'default_device_id': DEVICE_CATALOG.default_device_id, 'items': items},
            'request_id': getattr(request.state, 'request_id', '')}
