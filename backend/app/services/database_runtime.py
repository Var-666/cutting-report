"""Publish database service snapshots without changing operations already in flight."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, cast

from app.config import LOADED_SOURCES, Settings
from app.device_config import ProductionLineCatalog, load_line_catalog
from app.services.production_lines import ProductionLineRuntime
from app.services.runtime_settings_store import (
    RuntimeSettingsSaveError, read_runtime_env, write_runtime_env, runtime_settings_lock,
)
from app.services.database_configuration import (
    DatabaseConfigurationRequest, WeldingConnectionRequest,
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
    from app.services.file_download_service import FileDownloadService
    from app.services.worker_auth_service import WorkerAuthService


@dataclass(frozen=True)
class DatabaseServices:
    """One published service graph, retained by operations already in flight."""
    settings: Settings
    catalog: ProductionLineCatalog
    lines: dict[str, ProductionLineRuntime]
    worker_auth: WorkerAuthService
    file_downloads: FileDownloadService
    cutting_dispatch: CuttingDispatchService
    database_sources: DatabaseSourceRegistry = field(default_factory=DatabaseSourceRegistry)


DATABASE_FIELDS = {'data_source_mode', 'worker_database_auth_enabled', 'file_database_enabled',
                   'database_sources_json', 'production_lines_json', 'worker_source_key', 'file_source_key', 'dispatch_source_key'}
DATABASE_SETTINGS = {item.metadata['env']: item.name for item in fields(Settings) if item.name in DATABASE_FIELDS}
DATABASE_DIAGNOSTIC_KEYS = set(DATABASE_SETTINGS) | {'APP_EFFECTIVE_DATA_SOURCE_MODE'}


def parse_database_settings(base: Settings, values: dict[str, object]) -> Settings:
    changes = {}
    for key, name in DATABASE_SETTINGS.items():
        if key not in values:
            continue
        value = str(values[key])
        if name in {'worker_database_auth_enabled', 'file_database_enabled'}:
            if value.lower() not in {'1', 'true', 'yes', 'on', '0', 'false', 'no', 'off'}:
                raise ValueError('数据库启用状态无效')
            value = value.lower() in {'1', 'true', 'yes', 'on'}
        if isinstance(value, str) and any(char in value for char in ('\r', '\n', '\x00')):
            raise ValueError('数据库配置不能包含控制字符')
        changes[name] = value
    updated = replace(base, **changes)
    load_line_catalog(updated.production_lines_json)
    return updated


def source_bindings(settings: Settings, catalog: ProductionLineCatalog) -> dict[str, tuple[str, ...]]:
    result = catalog.source_bindings()
    for key, reference in ((settings.worker_source_key, "工人 HR"), (settings.file_source_key, "切割文件"),
                           (settings.dispatch_source_key, "魔盒配置"), ("bm", "切割报表船名关联")):
        if key:
            result[key] = (*result.get(key, ()), reference)
    return result


def validate_sources(configuration: Settings, catalog: ProductionLineCatalog, sources: DatabaseSourceRegistry) -> None:
    sources.validate_lines(catalog, demo=configuration.effective_data_source_mode == 'demo')
    for key, enabled in ((configuration.worker_source_key, configuration.worker_database_auth_enabled),
                         (configuration.file_source_key, configuration.file_database_enabled),
                         (configuration.dispatch_source_key, configuration.magic_box_enabled)):
        if not key:
            if enabled:
                raise ValueError('启用的共享业务必须指定数据源绑定')
            continue
        config = sources.get(key)
        if config.engine != 'sqlserver' or enabled and not config.enabled:
            raise SourceConflict('共享业务必须绑定兼容且已启用的 SQL Server 数据源')


class DatabaseRuntime:
    def __init__(self, settings: Settings, context: DatabaseServices,
                 build: Callable[[Settings, DatabaseSourceRegistry], DatabaseServices], automation_runtime: AutomationRuntime,
                 ) -> None:
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
        registry = DatabaseSourceRegistry.from_json(updated.database_sources_json,
            previous=previous.database_sources, bindings=source_bindings(updated, load_line_catalog(updated.production_lines_json)))
        validate_sources(updated, load_line_catalog(updated.production_lines_json), registry)
        context = self._build(updated, registry)
        return replace(context, database_sources=registry)

    def _saved_sources(self, saved: dict) -> DatabaseSourceRegistry:
        cfg = parse_database_settings(self._base, saved)
        return DatabaseSourceRegistry.from_json(cfg.database_sources_json, bindings=source_bindings(cfg, load_line_catalog(cfg.production_lines_json)))

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

    def save_configuration(self, payload: DatabaseConfigurationRequest | WeldingConnectionRequest, *, line_id: str | None = None) -> None:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            saved = read_runtime_env(path)
            cfg = parse_database_settings(self._base, saved)
            registry = self._saved_sources(saved)
            catalog = load_line_catalog(cfg.production_lines_json)
            line = catalog.get(line_id) if line_id else None
            values: dict[str, object] = {}
            configs = {config.id: config for config in registry.configs()}

            def update_source(key, connection, *, enabled=None):
                if not key:
                    if any((connection.host, connection.database, connection.username)):
                        raise ValueError("请先配置数据源绑定")
                    return
                old = registry.get(key)
                changes = connection.model_dump(exclude={"password", "enabled", "timezone"})
                if enabled is not None:
                    changes["enabled"] = enabled
                request = DatabaseSourceRequest.model_validate({**old.model_dump(exclude={"password"}), **changes,
                                                               "password": connection.password})
                candidate = self._source_config(request, registry)
                changes = {field: getattr(candidate, field) for field in type(candidate).model_fields
                           if getattr(candidate, field) != getattr(old, field)}
                for field, value in changes.items():
                    if getattr(configs[key], field) not in (getattr(old, field), value):
                        raise ValueError("同一数据源在多个配置区块存在冲突修改")
                configs[key] = configs[key].model_copy(update=changes)

            if isinstance(payload, DatabaseConfigurationRequest):
                values.update({"APP_DATA_SOURCE_MODE": payload.data_source_mode,
                               "WORKER_DATABASE_AUTH_ENABLED": str(payload.worker_database_auth_enabled).lower(),
                               "FILE_DATABASE_ENABLED": str(payload.file_database_enabled).lower()})
                if line and line.type == "cutting":
                    update_source(line.source_key, payload.production, enabled=True if payload.data_source_mode == "sqlserver" else None)
                elif any((payload.production.host, payload.production.database, payload.production.username)):
                    raise ValueError("必须明确选择切割产线")
                update_source(cfg.worker_source_key, payload.worker, enabled=True if payload.worker_database_auth_enabled else None)
                update_source(cfg.file_source_key, payload.file, enabled=True if payload.file_database_enabled else None)
                welding = payload.welding
            else:
                welding = payload
            if welding is not None:
                if line is None or line.type != "welding":
                    raise ValueError("必须明确选择焊接产线")
                update_source(line.source_key, welding, enabled=True if welding.enabled else None)
                updated = line.model_copy(update={"enabled": welding.enabled, "timezone": welding.timezone})
                catalog = catalog.model_copy(update={"lines": tuple(updated if item.id == line.id else item for item in catalog.lines)})
                values["PRODUCTION_LINES_JSON"] = catalog.model_dump_json()
            values["DATABASE_SOURCES_JSON"] = source_configs_json(list(configs.values()))
            merged = {**saved, **values}
            prepared = self.prepare(merged)
            try:
                write_runtime_env(path, values)
            except (OSError, ValueError):
                raise RuntimeSettingsSaveError('运行配置保存失败') from None
            self.publish(prepared, merged)

    def save_lines(self, catalog: ProductionLineCatalog) -> None:
        path = self._base.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            saved = read_runtime_env(path)
            previous = load_line_catalog(parse_database_settings(self._base, saved).production_lines_json)
            values = {**saved, "PRODUCTION_LINES_JSON": catalog.model_dump_json()}
            # Removing IDs makes persisted jobs unreachable; retire instances by disabling them.
            if {line.id for line in previous.lines} - {line.id for line in catalog.lines}:
                raise ValueError("已有产线只能停用，不能删除历史标识")
            for line in catalog.lines:
                old = next((item for item in previous.lines if item.id == line.id), None)
                if old and (old.type, old.device_id, old.machine_no) != (line.type, line.device_id, line.machine_no):
                    raise ValueError("已有产线类型与物理设备标识不能改变")
            prepared = self.prepare(values)
            write_runtime_env(path, {"PRODUCTION_LINES_JSON": catalog.model_dump_json()})
            self.publish(prepared, values)

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
