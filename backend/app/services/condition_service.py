"""Common field registry, validation, parameterized SQL and offline evaluator.

Register a business dataset here and attach its query adapter. Field expressions
are server-owned; neither expression nor dialect is accepted from an API caller.
"""
from dataclasses import dataclass
from datetime import date
import math
from typing import Any

from app.models.conditions import ConditionGroup


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
        return {"key": self.key, "label": self.label, "type": self.type,
                "operators": [{"value": op, "label": OPERATORS[op]} for op in self.operators],
                "options": [{"value": value, "label": label} for value, label in self.options], "hint": self.hint}


def fields_for(dataset, settings=None):
    if dataset == "cutting_files":
        sent = settings.file_status_sent_value if settings else "已派发"
        failed = settings.file_status_failed_value if settings else "派发失败"
        return (
            FieldSpec("ship_no", "船号", "text", "p.ShipNo"),
            FieldSpec("project_name", "工程名称", "text", "p.ProjectInfoName"),
            FieldSpec("document_no", "文档 / 文件名", "text", "a.DocumentNo"),
            FieldSpec("catalog_title", "目录名称", "text", "c.Title"),
            FieldSpec("pbs_info", "中间产品 / 分段", "text", "a.PBSInfo"),
            FieldSpec("version", "文档版本", "text", "v.Version"),
            FieldSpec("physical_version_no", "物理版本序号", "number", "TRY_CONVERT(decimal(18,4), physical.VersionNo)"),
            FieldSpec("operator", "操作人", "text", "a.Operator"),
            FieldSpec("operator_job_no", "操作人工号", "text", "a.OperatorJobNo"),
            FieldSpec("operate_date", "操作日期", "date", "TRY_CONVERT(date, a.OperateTime)", "operate_time"),
            FieldSpec("dispatch_state", "派发状态", "enum", "a.Custom6", "custom6",
                      ((sent, "已派发"), (failed, "派发失败")), "选择“为空”表示待派发；未识别状态不会自动归为已派发。"),
            FieldSpec("file_path", "文件相对路径", "text", "physical.FilePath"),
        )
    if dataset == "cutting_downloads":
        return (
            FieldSpec("filename", "本地文件名", "text", "json_extract(payload, '$.filename')"),
            FieldSpec("document_no", "文档名称", "text", "json_extract(payload, '$.document_no')"),
            FieldSpec("version", "版本", "text", "json_extract(payload, '$.version')"),
            FieldSpec("relative_path", "来源相对路径", "text", "json_extract(payload, '$.relative_path')"),
            FieldSpec("created_by_name", "下载操作人", "text", "json_extract(payload, '$.created_by_name')"),
            FieldSpec("download_date", "下载日期", "date", "substr(created_at, 1, 10)", "created_at"),
            FieldSpec("size", "文件大小（字节）", "number", "json_extract(payload, '$.size')"),
            FieldSpec("status", "下载记录状态", "enum", "status", options=(("downloading", "下载中"), ("downloaded", "已下载"), ("failed", "下载失败"))),
            FieldSpec("source_mode", "文件来源", "enum", "json_extract(payload, '$.source_mode')", options=(("http", "FO HTTP 文件服务"), ("local", "本地目录"), ("mock", "模拟来源"))),
        )
    raise ConditionError("不支持的查询对象")


def schema_for(dataset, settings=None):
    fields = fields_for(dataset, settings)
    return {"dataset": dataset, "label": {"cutting_files": "PDM 文件", "cutting_downloads": "已下载文件"}[dataset],
            "requires_filter": dataset == "cutting_files", "max_rules": 50, "max_values": 50, "max_page_size": 100,
            "fields": [field.public() for field in fields],
            "logic_options": [{"value": "and", "label": "全部满足（AND）"}, {"value": "or", "label": "任一满足（OR）"}]}


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


def _like(value):
    # SQL Server also treats '[' as a LIKE metacharacter. Escape it for literal search.
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_").replace("[", "![")


def compile_conditions(group: ConditionGroup, fields):
    clauses, parameters = [], []
    for field, operator, values in prepare(group, fields):
        column = field.expression
        if operator in {"is_empty", "not_empty"}:
            empty = f"({column} IS NULL OR LTRIM(RTRIM({column})) = '')" if field.type in {"text", "enum"} else f"({column} IS NULL)"
            clauses.append(empty if operator == "is_empty" else f"NOT {empty}")
        elif operator in {"contains", "not_contains", "starts_with", "ends_with"}:
            literal = _like(values[0])
            pattern = ("" if operator == "starts_with" else "%") + literal + ("" if operator == "ends_with" else "%")
            clauses.append(f"{column} {'NOT LIKE' if operator == 'not_contains' else 'LIKE'} ? ESCAPE '!'")
            parameters.append(pattern)
        elif operator in {"in", "not_in"}:
            clauses.append(f"{column} {'NOT IN' if operator == 'not_in' else 'IN'} ({', '.join('?' for _ in values)})")
            parameters.extend(values)
        elif operator == "between":
            clauses.append(f"{column} BETWEEN ? AND ?")
            parameters.extend(values)
        else:
            token = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[operator]
            clauses.append(f"{column} {token} ?")
            parameters.extend(values)
    return ("(" + (" AND " if group.logic == "and" else " OR ").join(clauses) + ")" if clauses else "1 = 1"), parameters


def matches_conditions(row: dict[str, Any], group: ConditionGroup, fields):
    """Offline/mock evaluator using the same validation and null semantics."""
    matches = []
    for field, operator, values in prepare(group, fields):
        current = row.get(field.source_key or field.key)
        if field.type == "date" and current is not None:
            current = str(current)[:10]
        if field.type == "number" and current is not None:
            try:
                current = float(current)
                if not math.isfinite(current):
                    current = None
            except (TypeError, ValueError):
                current = None
        if operator in {"is_empty", "not_empty"}:
            empty = current is None or (field.type in {"text", "enum"} and not str(current).strip())
            matches.append(empty if operator == "is_empty" else not empty)
            continue
        if current is None:
            matches.append(False)
            continue
        if field.type in {"text", "enum"}:
            current, values = str(current).casefold(), [str(value).casefold() for value in values]
        value = values[0]
        match operator:
            case "eq": matched = current == value
            case "ne": matched = current != value
            case "contains": matched = value in current
            case "not_contains": matched = value not in current
            case "starts_with": matched = current.startswith(value)
            case "ends_with": matched = current.endswith(value)
            case "in": matched = current in values
            case "not_in": matched = current not in values
            case "between": matched = values[0] <= current <= values[1]
            case "gt": matched = current > value
            case "gte": matched = current >= value
            case "lt": matched = current < value
            case "lte": matched = current <= value
        matches.append(matched)
    return not matches or (all(matches) if group.logic == "and" else any(matches))
