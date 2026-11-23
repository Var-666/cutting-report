"""Build typed runtime shapes with explicit test dependencies."""
from dataclasses import fields, replace
from typing import NoReturn

from fastapi import FastAPI

from app.runtime.application import ApplicationRuntime
from app.runtime.database import DatabaseServices
from app.modules.cutting.runtime import DeviceRuntime


class _MissingDependency:
    def __init__(self, field: str) -> None:
        self.field = field

    def _fail(self, *_args, **_kwargs) -> NoReturn:
        raise AssertionError(f"测试未提供 {self.field}")

    __getattr__ = _fail
    __call__ = _fail
    __bool__ = _fail
    __getitem__ = _fail
    __iter__ = _fail
    __len__ = _fail


def _missing_fields(runtime_type) -> dict:
    return {field.name: _MissingDependency(field.name) for field in fields(runtime_type)}


_APPLICATION = ApplicationRuntime(**_missing_fields(ApplicationRuntime))
_DATABASE = DatabaseServices(**_missing_fields(DatabaseServices))
_DEVICE = DeviceRuntime(**_missing_fields(DeviceRuntime))


def make_application_runtime(**overrides) -> ApplicationRuntime:
    return replace(_APPLICATION, **overrides)


def make_database_services(**overrides) -> DatabaseServices:
    return replace(_DATABASE, **overrides)


def make_device_runtime(**overrides) -> DeviceRuntime:
    return replace(_DEVICE, **overrides)


def install_runtime(app: FastAPI, application_runtime: ApplicationRuntime) -> None:
    app.state.runtime = application_runtime


def make_pipeline_stub(**services):
    from types import SimpleNamespace

    known = ("query_service", "range_service", "energy_service", "energy_period_service")
    return SimpleNamespace(**({name: _MissingDependency(f"pipeline.{name}") for name in known} | services))
