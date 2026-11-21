"""Install explicit runtime snapshots in applications without a Web lifespan."""
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from fastapi import FastAPI, Request
import pytest

from app.api.workloads import build_workloads
from app.config import Settings
from app.core.sql_capacity import SqlCapacity
from app.database.source_registry import DatabaseSourceRegistry
from app.device_config import ProductionLineCatalog, load_line_catalog
from app.services.database_runtime import DatabaseServices
from app.services.device_runtime import DeviceRuntime
from tests.line_fixtures import configuration, runtime_line
from tests.runtime_factories import install_runtime, make_application_runtime, make_database_services


@dataclass(frozen=True)
class FixedDatabaseRuntime:
    current_context: DatabaseServices

    def snapshot(self) -> DatabaseServices:
        return self.current_context


def database_services(app: FastAPI) -> DatabaseServices:
    return app.state.runtime.database_runtime.current_context


def replace_database_services(app: FastAPI, **changes) -> None:
    app.state.runtime.database_runtime = FixedDatabaseRuntime(replace(database_services(app), **changes))


def configure_web(app: FastAPI, settings: Settings, *, monkeypatch=None) -> None:
    sources = DatabaseSourceRegistry.from_json(settings.database_sources_json, capacity=SqlCapacity(settings))
    pools = build_workloads(settings, sources.capacity)
    catalog = (load_line_catalog(settings.production_lines_json) if settings.production_lines_json
               else ProductionLineCatalog(lines=()))
    context = make_database_services(settings=settings, database_sources=sources,
        catalog=catalog, lines={}, shared_reports={})
    runtime = make_application_runtime(
        database_runtime=FixedDatabaseRuntime(context),
        worker_status_reader=SimpleNamespace(status=lambda: {"state": "missing", "report_alive": False}),
        operations=SimpleNamespace(repeated_http_failure=lambda *_: False),
    )
    if monkeypatch is None:
        install_runtime(app, runtime)
        app.state.workloads = pools
    else:
        monkeypatch.setattr(app.state, 'runtime', runtime, raising=False)
        monkeypatch.setattr(app.state, 'workloads', pools, raising=False)


def embedded_app(settings: Settings | None = None, **kwargs) -> FastAPI:
    app = FastAPI(**kwargs)
    configure_web(app, settings or configuration(Path('/tmp/cutting-web-test')))
    return app


def request_for(app: FastAPI, *, path="/", method="GET", params=(), headers=None,
                request_id="test", root_path="") -> Request:
    return Request({
        "type": "http", "app": app, "method": method, "path": path, "root_path": root_path,
        "query_string": urlencode(params, doseq=True).encode(),
        "headers": [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()],
        "state": {"request_id": request_id},
    })


def cutting_request(device: DeviceRuntime, *, request_id="test") -> Request:
    app = embedded_app(device.settings)
    line_id = device.settings.device_id
    replace_database_services(app, lines={line_id: runtime_line(device, line_id)})
    return request_for(app, params={'device_id': line_id}, request_id=request_id)


@pytest.fixture
def shared_web(tmp_path, monkeypatch):
    from app.main import app
    configure_web(app, configuration(tmp_path), monkeypatch=monkeypatch)
    return app
