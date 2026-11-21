"""Explicit configuration for embedded test applications without Web lifespan."""
from pathlib import Path

from fastapi import FastAPI
import pytest

from app.api.workloads import build_workloads
from app.config import Settings
from app.core.sql_capacity import SqlCapacity
from app.database.source_registry import DatabaseSourceRegistry
from tests.line_fixtures import configuration


def configure_web(app: FastAPI, settings: Settings, *, monkeypatch=None) -> None:
    sources = DatabaseSourceRegistry.from_json(settings.database_sources_json, capacity=SqlCapacity(settings))
    pools = build_workloads(settings, sources.capacity)
    if monkeypatch is None:
        app.state.settings = settings
        app.state.workloads = pools
        app.state.database_sources = sources
    else:
        monkeypatch.setattr(app.state, 'settings', settings, raising=False)
        monkeypatch.setattr(app.state, 'workloads', pools, raising=False)
        monkeypatch.setattr(app.state, 'database_sources', sources, raising=False)


def embedded_app(settings: Settings | None = None, **kwargs) -> FastAPI:
    app = FastAPI(**kwargs)
    configure_web(app, settings or configuration(Path('/tmp/cutting-web-test')))
    return app


@pytest.fixture
def shared_web(tmp_path, monkeypatch):
    from app.main import app
    configure_web(app, configuration(tmp_path), monkeypatch=monkeypatch)
    return app
