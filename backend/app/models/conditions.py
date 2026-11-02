"""Shared, bounded query contract. No client-provided SQL or column expressions."""
from dataclasses import dataclass
from datetime import date
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ConditionRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=32)
    value: str | int | float | None | list[str | int | float] = None


class ConditionGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    logic: Literal["and", "or"] = "and"
    rules: list[ConditionRule] = Field(default_factory=list, max_length=50)


class SortRule(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str = Field(min_length=1, max_length=64)
    direction: Literal["asc", "desc"] = "asc"


class ConditionSearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    filters: ConditionGroup = Field(default_factory=ConditionGroup)
    page: int = Field(default=1, ge=1, le=100000)
    page_size: int = Field(default=10, ge=1, le=100)
    sort: list[SortRule] = Field(default_factory=list, max_length=5)


OPERATORS = {
    "eq": "等于", "ne": "不等于", "contains": "包含", "not_contains": "不包含",
    "starts_with": "开头是", "ends_with": "结尾是", "in": "属于其中之一", "not_in": "不属于其中任何一个",
    "gt": "大于 / 晚于", "gte": "大于等于 / 不早于", "lt": "小于 / 早于", "lte": "小于等于 / 不晚于",
    "between": "介于（含边界）", "is_empty": "为空", "not_empty": "不为空",
}

TEXT_OPS = ("contains", "eq", "ne", "not_contains", "starts_with", "ends_with", "in", "not_in", "is_empty", "not_empty")

ORDERED_OPS = ("eq", "ne", "gt", "gte", "lt", "lte", "between", "in", "not_in", "is_empty", "not_empty")

ENUM_OPS = ("eq", "ne", "in", "not_in", "is_empty", "not_empty")


class ConditionError(ValueError):
    pass

@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    type: str
    expression: str
    source_key: str = ""
    options: tuple[tuple[str, str], ...] = ()
    hint: str = ""

    @property
    def operators(self):
        return ENUM_OPS if self.type == "enum" else ORDERED_OPS if self.type in {"number", "date"} else TEXT_OPS

    def public(self):
        if self.key in {"project_id", "ship_no", "project_name"}:
            group = "工程信息"
        elif self.key.startswith("catalog_") or self.key == "classification_type":
            group = "文件分类 / 分组"
        elif self.key.startswith("physical_") or self.key == "file_path":
            group = "物理文件"
        elif self.key.startswith("downloaded_") or self.key == "dispatch_state":
            group = "PDM下载 / 派发"
        elif self.key in {"filename", "relative_path", "created_by_name", "download_date", "size", "status", "source_mode"}:
            group = "本地下载记录"
        else:
            group = "设计文档"
        return {"key": self.key, "label": self.label, "type": self.type,
                "operators": [{"value": op, "label": OPERATORS[op]} for op in self.operators],
                "options": [{"value": value, "label": label} for value, label in self.options],
                "hint": self.hint, "group": group}

def _value(value, field):
    if value is None or isinstance(value, (list, dict, bool)):
        raise ConditionError(f"{field.label}：请填写有效的条件值")
    if field.type == "number":
        try:
            converted = float(value)
            if not math.isfinite(converted) or abs(converted) > 10 ** 14:
                raise ValueError()
            return converted
        except (ValueError, TypeError, OverflowError):
            raise ConditionError(f"{field.label}：需要有效数字，绝对值不超过10的14次方") from None
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ConditionError(f"{field.label}：需要1—500字的条件值；查空值请使用“为空”")
    value = value.strip()
    if field.type == "date":
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or not 1900 <= parsed.year <= 2100:
                raise ValueError()
        except ValueError:
            raise ConditionError(f"{field.label}：请填写1900—2100年的 YYYY-MM-DD 日期") from None
    if field.type == "enum" and value not in {item[0] for item in field.options}:
        raise ConditionError(f"{field.label}：请从允许的选项中选择")
    return value

def prepare(group: ConditionGroup, fields):
    allowed = {field.key: field for field in fields}
    result = []
    for index, rule in enumerate(group.rules, 1):
        field = allowed.get(rule.field)
        if field is None:
            raise ConditionError(f"第{index}条条件的字段不可查询")
        if rule.operator not in field.operators:
            raise ConditionError(f"{field.label}：不支持该比较方式")
        if rule.operator in {"is_empty", "not_empty"}:
            if rule.value not in (None, ""):
                raise ConditionError(f"{field.label}：空值条件不能附带比较值")
            values = []
        elif rule.operator in {"between", "in", "not_in"}:
            if not isinstance(rule.value, list) or not 1 <= len(rule.value) <= 50:
                raise ConditionError(f"{field.label}：请提供1—50个条件值")
            values = [_value(value, field) for value in rule.value]
            if rule.operator == "between" and (len(values) != 2 or values[0] > values[1]):
                raise ConditionError(f"{field.label}：区间需要起止两个值，且起始值不能大于结束值")
        else:
            values = [_value(rule.value, field)]
        result.append((field, rule.operator, values))
    if sum(len(values) for _field, _operator, values in result) > 1000:
        raise ConditionError("单次查询的比较值总数不能超过1000，请减少条件或分批查询")
    return result
