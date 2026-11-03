from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel, ConfigDict, Field, StringConstraints, ValidationError,
    field_validator, model_validator,
)


ConfigText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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


class ProductionLineInstance(ConfigModel):
    id: ConfigText
    name: ConfigText
    type: Literal["cutting", "welding"]
    source_key: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    enabled: bool = True
    # Physical identity is separate from the stable line key used by historical device_id columns.
    device_id: ConfigText | None = None
    production_line_id: ConfigText | None = None
    machine_no: ConfigText | None = None
    energy: DeviceEnergyConfig | None = None
    alarm: DeviceAlarmConfig | None = None
    timezone: ConfigText = "Asia/Shanghai"
    device_nos: tuple[int, ...] = ()

    @model_validator(mode="after")
    def validate_business_scope(self):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("生产时区无效") from None
        if self.type == "cutting":
            if not all((self.device_id, self.production_line_id, self.machine_no, self.alarm)):
                raise ValueError("切割线必须指定设备、生产线、机床和告警起点")
            if self.device_nos:
                raise ValueError("切割线不使用焊接设备范围")
            if self.energy and self.machine_no.casefold() not in {x.casefold() for x in self.energy.equipment_ids}:
                raise ValueError("能耗设备范围必须包含生产设备编号")
        elif any((self.device_id, self.machine_no, self.energy, self.alarm)):
            raise ValueError("焊接线不使用切割设备配置")
        if len(set(self.device_nos)) != len(self.device_nos) or any(x < 0 for x in self.device_nos):
            raise ValueError("焊接设备范围必须唯一且非负")
        return self


class ProductionLineCatalog(ConfigModel):
    schema_version: Literal[2] = 2
    lines: tuple[ProductionLineInstance, ...]
    default_line_id: ConfigText | None = None

    @model_validator(mode="after")
    def validate_references(self):
        if len({line.id.casefold() for line in self.lines}) != len(self.lines):
            raise ValueError("产线编号必须唯一")
        if self.default_line_id and self.default_line_id not in {line.id for line in self.lines}:
            raise ValueError("默认产线不存在")
        physical = set()
        machines = set()
        meters = set()
        scopes: dict[tuple[str, str], list[ProductionLineInstance]] = {}
        for line in self.lines:
            if line.type == "cutting":
                if line.device_id.casefold() in physical:
                    raise ValueError("物理设备不能重复登记")
                physical.add(line.device_id.casefold())
                aliases = {x.casefold() for x in (line.machine_no, *(line.energy.equipment_ids if line.energy else ()))}
                if machines & aliases:
                    raise ValueError("物理机床编号及历史别名不能在多条产线重复使用")
                machines.update(aliases)
                for meter in ((line.energy.gas_parameter_id, *line.energy.electricity_parameter_ids) if line.energy else ()):
                    if meter in meters:
                        raise ValueError("不同产线不能共用同一仪表")
                    meters.add(meter)
            siblings = scopes.setdefault((line.type, line.source_key), [])
            for other in siblings:
                if line.type == "cutting":
                    own = {x.casefold() for x in (line.machine_no, *(line.energy.equipment_ids if line.energy else ()))}
                    theirs = {x.casefold() for x in (other.machine_no, *(other.energy.equipment_ids if other.energy else ()))}
                    overlap = bool(own & theirs)
                else:
                    overlap = not line.device_nos or not other.device_nos or bool(set(line.device_nos) & set(other.device_nos))
                if overlap:
                    raise ValueError("共用数据源的同类型产线必须指定互不重叠的设备范围")
            siblings.append(line)
        return self

    def get(self, line_id: str) -> ProductionLineInstance:
        line = next((line for line in self.lines if line.id == line_id), None)
        if line is None:
            raise ValueError("产线未登记")
        return line

    def source_bindings(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, tuple[str, ...]] = {}
        for line in self.lines:
            result[line.source_key] = (*result.get(line.source_key, ()), f"产线:{line.id}")
        return result


def load_line_catalog(value: str) -> ProductionLineCatalog:
    if not value:
        raise ValueError("缺少 PRODUCTION_LINES_JSON，请先运行 scripts/migrate_production_lines.py")
    try:
        return ProductionLineCatalog.model_validate_json(value)
    except ValidationError:
        raise ValueError("产线配置无效，请核对唯一 ID、类型和设备范围") from None


class PublicProductionLine(ConfigModel):
    id: str
    name: str
    type: Literal["cutting", "welding"]
    source_key: str
    enabled: bool
    type_name: str
    capabilities: tuple[str, ...]
    home_path: str
    device_id: str | None = None
    production_line_id: str | None = None
    machine_no: str | None = None
    energy_connected: bool = False
    alarm_start_at: str | None = None


class PublicLineCatalog(ConfigModel):
    default_device_id: str
    items: list[PublicProductionLine]
