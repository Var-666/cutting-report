"""Public line registration and capability hints; user roles remain API dependencies."""
from typing import Any

from app.config import DEVICE_CATALOG


LINE_TYPES = {
    'cutting': {'type_name': '切割', 'home_path': '/admin/home',
                'capabilities': ['reports', 'alarms', 'dispatch', 'settings'],
                'api_modules': ('cutting', 'downloads', 'alarms', 'automation', 'workshop',
                                'leadership-report', 'equipment-report', 'energy-report', 'analysis',
                                'reports', 'options', 'projects', 'ships', 'conditions', 'designer')},
    'welding': {'type_name': '焊接', 'home_path': '/admin/welding/home',
                'capabilities': ['reports', 'analysis', 'settings'], 'api_modules': ('welding',)},
}
SERVICE_LINES = (
    {'id': 'welding', 'name': '小组立焊接线', 'type': 'welding',
     'service_key': 'welding', 'enabled_setting': 'weld_enabled'},
)


def registered_lines(context: Any = None) -> list[dict]:
    items = [{'id': device.id, 'name': device.name, 'machine_no': device.machine_no,
              'production_line_id': device.production_line_id, 'energy_connected': device.energy is not None,
              'alarm_start_at': device.alarm.start_at, 'type': 'cutting', 'enabled': True}
             for device in DEVICE_CATALOG.devices]
    for line in SERVICE_LINES:
        service = getattr(context, line['service_key'], None)
        items.append({key: line[key] for key in ('id', 'name', 'type')} | {
            'enabled': bool(service and getattr(service.settings, line['enabled_setting']))})
    return [item | {key: value for key, value in LINE_TYPES[item['type']].items() if key != 'api_modules'}
            for item in items]


def supports_path(line_id: str, path: str) -> bool:
    module = path.removeprefix('/api/v1/').split('/')[0]
    business_modules = {module for definition in LINE_TYPES.values() for module in definition['api_modules']}
    if not path.startswith('/api/v1/') or module not in business_modules:
        return True
    line = next((item for item in registered_lines() if item['id'] == line_id), None)
    return bool(line and module in LINE_TYPES[line['type']]['api_modules'])
