"""Immutable source snapshots with lazy, reusable engine-specific clients."""
from __future__ import annotations

import json
import hashlib
from threading import RLock
from types import MappingProxyType
from typing import Mapping, Sequence

from pydantic import TypeAdapter

from app.models.database_sources import DatabaseSourceConfig, DatabaseSourceView
from app.database.sqlserver import SqlServerClient
from app.database.mysql import MySqlClient

SOURCE_CONFIGS = TypeAdapter(list[DatabaseSourceConfig])
RESERVED_SOURCES = {'production', 'worker', 'file', 'welding', 'sqlite'}


class SourceNotFound(ValueError):
    pass


class SourceConflict(ValueError):
    pass


class DatabaseSourceRegistry:
    def __init__(self, configs: Sequence[DatabaseSourceConfig] = (), *,
                 bindings: Mapping[str, tuple[str, ...]] | None = None) -> None:
        sources = {config.id: config for config in configs}
        if len(sources) != len(configs) or sources.keys() & RESERVED_SOURCES:
            raise ValueError('数据源 ID 重复或使用了系统保留名称')
        if any(config.enabled and not config.password.get_secret_value() for config in configs):
            raise ValueError('启用的数据源必须配置密码')
        self._sources = MappingProxyType(sources)
        # Declared consumers can be deployed before their optional source is registered.
        self._bindings = MappingProxyType({key: refs for key, refs in (bindings or {}).items() if key in sources})
        self._clients: dict[str, SqlServerClient | MySqlClient] = {}
        self._lock = RLock()

    @classmethod
    def from_json(cls, value: str, *, previous: DatabaseSourceRegistry | None = None,
                  bindings: Mapping[str, tuple[str, ...]] | None = None) -> DatabaseSourceRegistry:
        registry = cls(SOURCE_CONFIGS.validate_json(value), bindings=bindings)
        if previous is not None:
            with previous._lock:
                registry._clients = {key: client for key, client in previous._clients.items()
                    if registry._sources.get(key) == previous._sources.get(key)}
        return registry

    def get(self, source_id: str) -> DatabaseSourceConfig:
        try:
            return self._sources[source_id]
        except KeyError:
            raise SourceNotFound('数据源不存在') from None

    def client(self, source_id: str) -> SqlServerClient | MySqlClient:
        config = self.get(source_id)
        with self._lock:
            if source_id not in self._clients:
                self._clients[source_id] = SqlServerClient(config) if config.engine == 'sqlserver' else MySqlClient(config)
            return self._clients[source_id]

    def fingerprint(self, source_id: str) -> str:
        return hashlib.sha256(source_configs_json([self.get(source_id)]).encode()).hexdigest()

    def connection_identity(self, source_id: str) -> str:
        config = self.get(source_id)
        value = config.model_dump(include={'id', 'engine', 'host', 'port', 'database', 'username'})
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def validate_lines(self, catalog, *, demo: bool = False) -> None:
        for line in catalog.lines:
            config = self.get(line.source_key)
            engine = {"cutting": "sqlserver", "welding": "mysql"}[line.type]
            if config.engine != engine:
                raise SourceConflict("产线类型与数据源引擎不兼容")
            if line.enabled and not config.enabled and not (demo and line.type == "cutting"):
                raise SourceConflict("启用的产线不能绑定停用的数据源")

    def view(self, source_id: str) -> DatabaseSourceView:
        config = self.get(source_id)
        configured = bool(config.password.get_secret_value())
        return DatabaseSourceView(**config.model_dump(exclude={'password'}),
            password_configured=configured, configured=bool(config.host and config.database and config.username and configured),
            references=list(self._bindings.get(source_id, ())))

    def list(self) -> list[DatabaseSourceView]:
        return [self.view(key) for key in sorted(self._sources)]

    def configs(self) -> list[DatabaseSourceConfig]:
        return list(self._sources.values())

    def require_unbound(self, source_id: str) -> None:
        self.get(source_id)
        if self._bindings.get(source_id):
            raise SourceConflict('数据源已被业务模块引用，不能删除或更换数据库类型')

    def close(self) -> None:
        with self._lock:
            for client in self._clients.values():
                if isinstance(client, MySqlClient):
                    client.close()


def source_configs_json(configs: list[DatabaseSourceConfig]) -> str:
    # SecretStr serialization is redacted by default; plaintext exists only in the protected env file.
    return json.dumps([{**config.model_dump(mode='json', exclude={'password'}),
        'password': config.password.get_secret_value()} for config in configs], ensure_ascii=False)
