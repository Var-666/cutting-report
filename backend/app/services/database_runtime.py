"""Publish database service snapshots without changing operations already in flight."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, cast

from app.config import LOADED_SOURCES, Settings
from app.services.runtime_settings_store import (
    RuntimeSettingsSaveError, read_runtime_env, write_runtime_env, runtime_settings_lock,
)
from app.services.database_configuration import (
    DatabaseConfigurationRequest, WeldingConnectionRequest, database_configuration_data,
    connection_env_values, welding_env_values,
)
from app.core.process_lock import settings_file_lock
from app.services.automation_runtime import AutomationRuntime
from app.models.database_sources import DatabaseSourceConfig, DatabaseSourceRequest
from app.database.source_registry import (
    DatabaseSourceRegistry, SourceConflict, SourceNotFound, source_configs_json,
)

if TYPE_CHECKING:
    from app.services.application_runtime import ApplicationRuntime
    from app.services.cutting_dispatch_service import CuttingDispatchService
    from app.services.device_runtime import DeviceRuntime
    from app.services.file_download_service import FileDownloadService
    from app.services.welding_service import WeldingService
    from app.services.worker_auth_service import WorkerAuthService


@dataclass(frozen=True)
class DatabaseServices:
    """One published service graph, retained by operations already in flight."""
    settings: Settings
    devices: dict[str, DeviceRuntime]
    welding: WeldingService
    worker_auth: WorkerAuthService
    file_downloads: FileDownloadService
    cutting_dispatch: CuttingDispatchService
    database_sources: DatabaseSourceRegistry = field(default_factory=DatabaseSourceRegistry)


CONNECTION_FIELDS = ('host', 'port', 'database', 'username', 'password', 'driver',
                     'encrypt', 'trust_server_certificate')
WELD_FIELDS = {'weld_enabled', 'weld_db_host', 'weld_db_port', 'weld_db_name', 'weld_db_user',
               'weld_db_password', 'weld_db_timezone'}
DATABASE_FIELDS = {'data_source_mode', 'worker_database_auth_enabled', 'file_database_enabled', 'database_sources_json'} | {
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
    if changes and changes.keys() <= WELD_FIELDS | {'database_sources_json'}:
        return updated
    for source, enabled in [('production', updated.effective_data_source_mode == 'sqlserver'),
                            ('worker', updated.worker_database_auth_enabled), ('file', updated.file_database_enabled)]:
        profile = updated.sqlserver_connection(source)
        if enabled and not all(profile[key] for key in ('host', 'database', 'username', 'password', 'driver')):
            raise ValueError('启用的数据库连接参数不完整')
    return updated


def welding_connection_settings(payload: WeldingConnectionRequest, base: Settings) -> Settings:
    path = base.runtime_settings_env_path
    with runtime_settings_lock, settings_file_lock(path):
        values = welding_env_values(payload, base, read_runtime_env(path))
    changes = {DATABASE_SETTINGS[key]: value for key, value in values.items()}
    return replace(base, **{**changes, 'weld_enabled': True})


class DatabaseRuntime:
    def __init__(self, settings: Settings, context: DatabaseServices,
                 build: Callable[[Settings, DatabaseSourceRegistry], DatabaseServices], automation_runtime: AutomationRuntime,
                 *, source_bindings: dict[str, tuple[str, ...]] | None = None) -> None:
        self._base = settings
        self._source_bindings = dict(source_bindings or {})
        self._context = replace(context, database_sources=DatabaseSourceRegistry.from_json(
            settings.database_sources_json, previous=context.database_sources, bindings=self._source_bindings))
        self._build = build
        self._automation_runtime = automation_runtime
        self._file_version = None
        self._revision = 0
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._base_sources = {key: LOADED_SOURCES.get(key, 'default') for key in DATABASE_SETTINGS}
        self._sources = dict(self._base_sources)
        self._error = None

    @property
    def current_context(self) -> DatabaseServices:
        """Read the published services without triggering a nested configuration reload."""
        with runtime_settings_lock:
            return self._context

    def _version(self) -> tuple[int, int, int] | None:
        try:
            stat = self._base.runtime_settings_env_path.stat()
            return stat.st_mtime_ns, stat.st_size, stat.st_ino
        except FileNotFoundError:
            return None

    def prepare(self, values: dict[str, object]) -> DatabaseServices:
        updated = parse_database_settings(self._base, values)
        if updated == self._context.settings:
            return self._context
        previous = self._context
        registry = previous.database_sources
        if updated.database_sources_json != previous.settings.database_sources_json:
            registry = DatabaseSourceRegistry.from_json(updated.database_sources_json,
                previous=registry, bindings=self._source_bindings)
        context = self._build(updated, registry)
        return replace(context, database_sources=registry)

    def _saved_sources(self, saved: dict) -> DatabaseSourceRegistry:
        return DatabaseSourceRegistry.from_json(saved.get('DATABASE_SOURCES_JSON', self._base.database_sources_json),
            bindings=self._source_bindings)

    def _source_config(self, payload: DatabaseSourceRequest, registry: DatabaseSourceRegistry) -> DatabaseSourceConfig:
        password = payload.password.get_secret_value() if payload.password else ''
        if not password:
            try:
                password = registry.get(payload.id).password.get_secret_value()
                if not password:
                    password = self._context.database_sources.get(payload.id).password.get_secret_value()
            except SourceNotFound:
                pass
        return DatabaseSourceConfig(**payload.model_dump(exclude={'password'}), password=password)

    def save_source(self, payload: DatabaseSourceRequest, *, create: bool = False) -> None:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            saved = read_runtime_env(path)
            registry = self._saved_sources(saved)
            exists = any(config.id == payload.id for config in registry.configs())
            if create and exists:
                raise SourceConflict('数据源 ID 已存在')
            if not create:
                old = registry.get(payload.id)
                if old.engine != payload.engine:
                    registry.require_unbound(payload.id)
            config = self._source_config(payload, registry)
            configs = [item for item in registry.configs() if item.id != config.id] + [config]
            self._save_sources(configs, saved)

    def set_source_enabled(self, source_id: str, enabled: bool) -> None:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            saved = read_runtime_env(path)
            registry = self._saved_sources(saved)
            config = registry.get(source_id).model_copy(update={'enabled': enabled})
            self._save_sources([item for item in registry.configs() if item.id != source_id] + [config], saved)

    def delete_source(self, source_id: str) -> None:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            saved = read_runtime_env(path)
            registry = self._saved_sources(saved)
            registry.require_unbound(source_id)
            self._save_sources([item for item in registry.configs() if item.id != source_id], saved)

    def _save_sources(self, configs: list[DatabaseSourceConfig], saved: dict) -> None:
        value = source_configs_json(configs)
        prepared = self.prepare({**saved, 'DATABASE_SOURCES_JSON': value})
        try:
            write_runtime_env(self._base.runtime_settings_env_path, {'DATABASE_SOURCES_JSON': value})
        except (OSError, ValueError):
            raise RuntimeSettingsSaveError('数据源配置保存失败') from None
        self.publish(prepared, {**saved, 'DATABASE_SOURCES_JSON': value})

    def source_for_test(self, payload: DatabaseSourceRequest) -> DatabaseSourceConfig:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            config = self._source_config(payload, self._saved_sources(read_runtime_env(path)))
        # A disabled source can be tested without enabling or saving it.
        config = config.model_copy(update={'enabled': True})
        DatabaseSourceRegistry([config])
        return config

    def close(self) -> None:
        self._context.database_sources.close()
        self._context.welding.repository.client.close()

    def save_configuration(self, payload: DatabaseConfigurationRequest | WeldingConnectionRequest) -> None:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            current = self.snapshot().settings
            saved = read_runtime_env(path)
            welding = payload if isinstance(payload, WeldingConnectionRequest) else payload.welding
            welding_only = isinstance(payload, WeldingConnectionRequest)
            if not welding_only and welding is not None:
                unchanged = DatabaseConfigurationRequest.model_validate({**database_configuration_data(current), 'welding': None})
                welding_only = payload.model_copy(update={'welding': None}) == unchanged
            values = {}
            if not welding_only:
                values = {
                    'APP_DATA_SOURCE_MODE': payload.data_source_mode,
                    'WORKER_DATABASE_AUTH_ENABLED': str(payload.worker_database_auth_enabled).lower(),
                    'FILE_DATABASE_ENABLED': str(payload.file_database_enabled).lower(),
                }
                for source in ('production', 'worker', 'file'):
                    values.update(connection_env_values(source, getattr(payload, source), current, saved))
            if welding is not None:
                values.update(welding_env_values(welding, current, saved))
            merged = {**saved, **values}
            prepared = self.prepare(merged)
            try:
                write_runtime_env(path, values)
            except (OSError, ValueError):
                raise RuntimeSettingsSaveError('运行配置保存失败') from None
            self.publish(prepared, merged)

    def publish(self, context: DatabaseServices, values: dict[str, object]) -> None:
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

    def snapshot(self) -> DatabaseServices:
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


def database_context(state: ApplicationRuntime) -> DatabaseServices:
    runtime = getattr(state, 'database_runtime', None)
    # Embedded apps can still supply their service graph without a hot runtime.
    return runtime.snapshot() if runtime is not None else cast(DatabaseServices, state)
