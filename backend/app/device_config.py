from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, ValidationError,
    field_validator, model_validator,
)


ConfigText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProductionLineConfig(ConfigModel):
    id: ConfigText
    name: ConfigText


class DeviceEnergyConfig(ConfigModel):
    equipment_ids: tuple[ConfigText, ...] = Field(min_length=1)
    gas_parameter_id: UUID
    electricity_parameter_ids: tuple[UUID, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bindings(self):
        if len({value.casefold() for value in self.equipment_ids}) != len(self.equipment_ids):
            raise ValueError("设备能耗别名不能重复")
        meters = (self.gas_parameter_id, *self.electricity_parameter_ids)
        if len(set(meters)) != len(meters):
            raise ValueError("气表和电表编号不能重复")
        return self


class DeviceAlarmConfig(ConfigModel):
    start_at: ConfigText

    @field_validator("start_at")
    @classmethod
    def validate_start_at(cls, value: str) -> str:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        return parsed.strftime("%Y-%m-%d %H:%M:%S")


class DeviceConfig(ConfigModel):
    id: ConfigText
    name: ConfigText
    production_line_id: ConfigText
    data_source: Literal["production"]
    machine_no: ConfigText
    energy: DeviceEnergyConfig | None = None
    alarm: DeviceAlarmConfig


class DeviceCatalog(ConfigModel):
    schema_version: Literal[1]
    default_device_id: ConfigText
    production_lines: tuple[ProductionLineConfig, ...] = Field(min_length=1)
    devices: tuple[DeviceConfig, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self):
        line_ids = {line.id for line in self.production_lines}
        device_ids = {device.id for device in self.devices}
        if (len({key.casefold() for key in line_ids}) != len(self.production_lines)
                or len({key.casefold() for key in device_ids}) != len(self.devices)):
            raise ValueError("产线编号和设备编号分别必须唯一")
        if self.default_device_id not in device_ids:
            raise ValueError("默认设备不存在")
        for device in self.devices:
            if device.production_line_id not in line_ids:
                raise ValueError("设备引用的产线不存在")
            if device.energy is not None and device.machine_no.casefold() not in {alias.casefold() for alias in device.energy.equipment_ids}:
                raise ValueError("能耗设备范围必须包含生产设备编号")
        return self

    @property
    def default_device(self) -> DeviceConfig:
        return next(device for device in self.devices if device.id == self.default_device_id)


def load_device_catalog(path: Path) -> DeviceCatalog:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"无法读取设备配置文件：{path}") from exc
    try:
        return DeviceCatalog.model_validate_json(content)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) or "文件结构"
                           for error in exc.errors(include_input=False, include_url=False))
        raise ValueError(f"设备配置文件校验失败：{path}（{fields}）") from None
