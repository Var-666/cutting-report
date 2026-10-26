"""Publish database service snapshots without changing operations already in flight."""
from dataclasses import fields, replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable

from app.config import LOADED_SOURCES, Settings
from app.services.runtime_settings_store import read_runtime_env, runtime_settings_lock
from app.services.process_lock import settings_file_lock


CONNECTION_FIELDS = ('host', 'port', 'database', 'username', 'password', 'driver',
                     'encrypt', 'trust_server_certificate')
WELD_FIELDS = {'weld_enabled', 'weld_db_host', 'weld_db_port', 'weld_db_name', 'weld_db_user',
               'weld_db_password', 'weld_db_timezone'}
DATABASE_FIELDS = {'data_source_mode', 'worker_database_auth_enabled', 'file_database_enabled'} | {
    f'{prefix}_{field}' for prefix in ('sqlserver', 'worker_sqlserver', 'file_sqlserver')
    for field in CONNECTION_FIELDS
} | WELD_FIELDS
DATABASE_SETTINGS = {item.metadata['env']: item.name for item in fields(Settings) if item.name in DATABASE_FIELDS}
DATABASE_DIAGNOSTIC_KEYS = set(DATABASE_SETTINGS) | {'APP_EFFECTIVE_DATA_SOURCE_MODE'} | {
    f'connections.{source}.{name}' for source in ('production', 'worker', 'file')
    for name in (*CONNECTION_FIELDS, 'uses_primary_fallback')
}


def parse_database_settings(base: Settings, values: dict[str, object]) -> Settings:
    changes = {}
    for key, name in DATABASE_SETTINGS.items():
        if key not in values:
            continue
        value = str(values[key])
        if name in {'worker_database_auth_enabled', 'file_database_enabled', 'weld_enabled'}:
            if value.lower() not in {'1', 'true', 'yes', 'on', '0', 'false', 'no', 'off'}:
                raise ValueError('数据库启用状态无效')
            value = value.lower() in {'1', 'true', 'yes', 'on'}
        elif name.endswith('_port'):
            value = int(value)
            if not 1 <= value <= 65535:
                raise ValueError('数据库端口无效')
        elif name.endswith(('_encrypt', '_trust_server_certificate')):
            if value and value.lower() not in {'yes', 'no', 'true', 'false', '1', '0', 'on', 'off'}:
                raise ValueError('数据库连接选项无效')
        if isinstance(value, str) and any(char in value for char in ('\r', '\n', '\x00')):
            raise ValueError('数据库配置不能包含控制字符')
        changes[name] = value
    updated = replace(base, **changes)
    if updated.weld_enabled:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        if not all((updated.weld_db_host, updated.weld_db_name, updated.weld_db_user, updated.weld_db_password)):
            raise ValueError('焊接连接配置不完整')
        try:
            ZoneInfo(updated.weld_db_timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError('生产时区无效') from None
    # A welding-only save preserves SQL Server profiles, including an unconfigured local preview.
    if changes and changes.keys() <= WELD_FIELDS:
        return updated
    for source, enabled in [('production', updated.effective_data_source_mode == 'sqlserver'),
                            ('worker', updated.worker_database_auth_enabled), ('file', updated.file_database_enabled)]:
        profile = updated.sqlserver_connection(source)
        if enabled and not all(profile[key] for key in ('host', 'database', 'username', 'password', 'driver')):
            raise ValueError('启用的数据库连接参数不完整')
    return updated


class DatabaseRuntime:
    def __init__(self, settings: Settings, context: SimpleNamespace,
                 build: Callable[[Settings], SimpleNamespace], automation_runtime) -> None:
        self._base = settings
        self._context = context
        self._build = build
        self._automation_runtime = automation_runtime
        self._file_version = None
        self._revision = 0
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._base_sources = {key: LOADED_SOURCES.get(key, 'default') for key in DATABASE_SETTINGS}
        self._sources = dict(self._base_sources)
        self._error = None

    @property
    def current_context(self) -> SimpleNamespace:
        """Read the published services without triggering a nested configuration reload."""
        with runtime_settings_lock:
            return self._context

    def _version(self) -> tuple[int, int, int] | None:
        try:
            stat = self._base.runtime_settings_env_path.stat()
            return stat.st_mtime_ns, stat.st_size, stat.st_ino
        except FileNotFoundError:
            return None

    def prepare(self, values: dict[str, object]) -> SimpleNamespace:
        updated = parse_database_settings(self._base, values)
        return self._context if updated == self._context.settings else self._build(updated)

    def publish(self, context: SimpleNamespace, values: dict[str, object]) -> None:
        with runtime_settings_lock:
            if context is not self._context:
                self._automation_runtime.apply(source='data/settings.env', **{
                    name: getattr(context.settings, name) for name in DATABASE_FIELDS
                })
                self._context = context
                self._revision += 1
            self._sources = {key: 'data/settings.env' if key in values else source
                             for key, source in self._base_sources.items()}
            self._loaded_at = datetime.now(timezone.utc).isoformat()
            self._file_version = self._version()
            self._error = None

    def snapshot(self) -> SimpleNamespace:
        with runtime_settings_lock:
            try:
                with settings_file_lock(self._base.runtime_settings_env_path):
                    version = self._version()
                    if version != self._file_version:
                        values = read_runtime_env(self._base.runtime_settings_env_path)
                        context = self.prepare(values)
                        self.publish(context, values)
            except ValueError:
                self._file_version = version
                self._error = '数据库配置无效，保留上次运行快照'
            except Exception:
                # Parser, filesystem and service errors must never reveal connection credentials.
                self._error = '数据库配置暂无法加载，保留上次运行快照'
            return self._context

    def inspect(self) -> dict:
        with runtime_settings_lock:
            return {'settings': self._context.settings, 'revision': self._revision,
                    'sources': dict(self._sources), 'loaded_at': self._loaded_at, 'error': self._error}


def database_context(state: Any) -> Any:
    runtime = getattr(state, 'database_runtime', None)
    return runtime.snapshot() if runtime is not None else state
