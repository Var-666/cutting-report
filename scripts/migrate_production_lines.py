"""One-time offline upgrade to PRODUCTION_LINES_JSON + DATABASE_SOURCES_JSON.

No database connection is opened. Stop Web and Worker before running this tool.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from app.configuration_sources import load_environment
from app.device_config import ProductionLineCatalog, load_line_catalog
from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
from app.models.database_sources import DatabaseSourceConfig
from app.core.process_lock import FileLock, settings_file_lock
from app.services.runtime_settings_store import write_runtime_env


def boolean(environment: dict[str, str], key: str, default: bool = False) -> bool:
    value = environment.get(key)
    if value is None:
        return default
    value = value.strip().lower()
    if value not in {'1', 'true', 'yes', 'on', '0', 'false', 'no', 'off'}:
        raise ValueError(f'{key} 必须是有效开关')
    return value in {'1', 'true', 'yes', 'on'}


def migrate_values(environment: dict[str, str], legacy: dict) -> dict[str, str]:
    """Prepare and validate everything before backing up or replacing configuration."""
    mode = (environment.get('APP_DATA_SOURCE_MODE') or ('demo' if boolean(environment, 'APP_DEMO_MODE', True) else 'sqlserver')).strip().lower()
    if mode not in {'demo', 'sqlserver'}:
        raise ValueError('旧生产数据模式无效')
    registry = DatabaseSourceRegistry.from_json(environment.get('DATABASE_SOURCES_JSON', '[]'))
    if environment.get('PRODUCTION_LINES_JSON'):
        registry.validate_lines(load_line_catalog(environment['PRODUCTION_LINES_JSON']), demo=mode == 'demo')
        return {}
    if legacy.get('schema_version') != 1 or not legacy.get('devices'):
        raise ValueError('旧设备目录结构无效')
    configs = registry.configs()

    def add(config: dict) -> str:
        key = config['id']
        suffix = 1
        while any(item.id == config['id'] for item in configs):
            suffix += 1
            config['id'] = f'{key}-{suffix}'
        configs.append(DatabaseSourceConfig.model_validate(config))
        return config['id']

    def sql_profile(prefix: str, key: str, enabled: bool) -> dict:
        return dict(id=key, name=key, engine='sqlserver', enabled=enabled,
            host=environment.get(f'{prefix}_HOST', ''), port=int(environment.get(f'{prefix}_PORT') or 1433),
            database=environment.get(f'{prefix}_DATABASE', ''), username=environment.get(f'{prefix}_USERNAME', ''),
            password=environment.get(f'{prefix}_PASSWORD', ''),
            driver=environment.get(f'{prefix}_DRIVER') or environment.get('SQLSERVER_DRIVER') or 'ODBC Driver 18 for SQL Server',
            encrypt=boolean(environment, f'{prefix}_ENCRYPT', boolean(environment, 'SQLSERVER_ENCRYPT', True)),
            trust_server_certificate=boolean(environment, f'{prefix}_TRUST_SERVER_CERTIFICATE', boolean(environment, 'SQLSERVER_TRUST_SERVER_CERTIFICATE', True)),
            connect_timeout=int(environment.get(f'{prefix}_CONNECT_TIMEOUT_SECONDS') or 5),
            query_timeout=int(environment.get(f'{prefix}_QUERY_TIMEOUT_SECONDS') or (120 if prefix == 'SQLSERVER' else 15)))

    worker_enabled = boolean(environment, 'WORKER_DATABASE_AUTH_ENABLED')
    file_enabled = boolean(environment, 'FILE_DATABASE_ENABLED')
    dispatch_enabled = boolean(environment, 'MAGIC_BOX_ENABLED')
    worker_dedicated = any(environment.get('WORKER_SQLSERVER_' + field) for field in ('HOST', 'DATABASE', 'USERNAME', 'PASSWORD'))
    file_dedicated = any(environment.get('FILE_SQLSERVER_' + field) for field in ('HOST', 'DATABASE', 'USERNAME', 'PASSWORD'))
    primary_enabled = mode == 'sqlserver' or dispatch_enabled or (worker_enabled and not worker_dedicated) or (file_enabled and not file_dedicated)
    primary = add(sql_profile('SQLSERVER', 'cutting-production', primary_enabled))
    weld_enabled = boolean(environment, 'WELD_ENABLED')
    welding = add(dict(id='welding-production', name='焊接生产库', engine='mysql', enabled=weld_enabled,
        host=environment.get('WELD_DB_HOST', ''), port=int(environment.get('WELD_DB_PORT') or 3306),
        database=environment.get('WELD_DB_NAME') or 'weld_production_data', username=environment.get('WELD_DB_USER', ''),
        password=environment.get('WELD_DB_PASSWORD', ''), pool_size=int(environment.get('WELD_DB_POOL_SIZE') or 4),
        connect_timeout=int(environment.get('WELD_DB_CONNECT_TIMEOUT_SECONDS') or 5), query_timeout=int(environment.get('WELD_DB_QUERY_TIMEOUT_SECONDS') or 120)))
    groups = {item['id'] for item in legacy['production_lines']}
    if len({key.casefold() for key in groups}) != len(legacy['production_lines']):
        raise ValueError('旧生产分组 ID 重复')
    lines = []
    for device in legacy['devices']:
        if device['production_line_id'] not in groups or device.get('data_source') != 'production':
            raise ValueError('旧设备产线或生产库引用无效')
        lines.append(dict(id=device['id'], name=device['name'], type='cutting', source_key=primary,
            enabled=True, device_id=device['id'], energy=device.get('energy'), **{key: device[key] for key in ('production_line_id', 'machine_no', 'alarm')}))
    lines.append(dict(id='welding', name='小组立焊接线', type='welding', source_key=welding,
                      enabled=weld_enabled, timezone=environment.get('WELD_DB_TIMEZONE') or 'Asia/Shanghai'))
    catalog = ProductionLineCatalog(lines=lines, default_line_id=legacy.get("default_device_id"))
    worker = add(sql_profile('WORKER_SQLSERVER', 'hr', worker_enabled)) if worker_dedicated else primary
    file = add(sql_profile('FILE_SQLSERVER', 'pdm', file_enabled)) if file_dedicated else primary
    updated = DatabaseSourceRegistry(configs)
    updated.validate_lines(catalog, demo=mode == 'demo')
    return {'PRODUCTION_LINES_JSON': catalog.model_dump_json(), 'DATABASE_SOURCES_JSON': source_configs_json(configs),
            'WORKER_SOURCE_KEY': worker, 'FILE_SOURCE_KEY': file, 'DISPATCH_SOURCE_KEY': primary}


def migrate(root: Path) -> bool:
    target = root / 'data' / 'settings.env'
    target.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        web = FileLock(root / 'data' / '.web-process.lock')
        if not web.acquire(blocking=False):
            raise ValueError('请先停止 Web')
        stack.callback(web.release)
        if (root / 'data' / 'metadata.db').exists():
            raise ValueError('此版本不再支持SQLite部署；请使用保留的旧版本先完成状态库迁移')
        stack.enter_context(settings_file_lock(target))
        environment, _, errors = load_environment(root, dict(os.environ))
        if errors:
            raise ValueError('旧配置读取失败，未执行迁移')
        legacy_path = Path(environment.get('DEVICE_CONFIG_PATH') or 'backend/app/configuration/devices.json').expanduser()
        if not legacy_path.is_absolute():
            legacy_path = root / legacy_path
        legacy = json.loads(legacy_path.read_text()) if not environment.get('PRODUCTION_LINES_JSON') else {}
        values = migrate_values(environment, legacy)
        if not values:
            return False
        backup = target.parent / ('p1-config-backup-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(mode=0o700)
        for name, path in (('settings.env', target), ('.env', root / '.env'), ('devices.json', legacy_path)):
            if path.is_file():
                with (backup / name).open('xb') as handle:
                    os.chmod(handle.name, 0o600)
                    handle.write(path.read_bytes())
        # Include effective legacy credentials sourced from process variables, not only files.
        old_keys = {key: value for key, value in environment.items() if key.startswith(('SQLSERVER_', 'WORKER_SQLSERVER_', 'FILE_SQLSERVER_', 'WELD_', 'DATABASE_SOURCES_'))}
        write_runtime_env(backup / 'effective-connections.env', old_keys)
        write_runtime_env(target, values)
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        changed = migrate(args.root.resolve())
    except (ValueError, OSError, KeyError):
        raise SystemExit('迁移失败：配置未发布。请核对旧设备目录、启用连接及已有统一配置；不要打印凭据。') from None
    print('迁移成功，已保护旧配置；启动 Web 和 Worker 使用统一配置。' if changed else '统一配置已存在且校验通过，无需重复迁移。')


if __name__ == '__main__':
    main()
