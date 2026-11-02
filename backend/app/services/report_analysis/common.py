"""Formatting and canonicalization shared by report analysis contexts."""
from __future__ import annotations

import hashlib
import json
import statistics
from decimal import Decimal

from app.models.domain import EquipmentRecord, WorkshopRecord, to_primitive


PLAIN_LANGUAGE_TERMS = (
    ("未标注工程", "工程号未填写"),
    ("未标注设备", "设备编号未填写"),
    ("高位生产日", "任务较多的日期"),
    ("高位日", "任务较多的日期"),
    ("工时异常", "切割时间过长"),
    ("数据边界", "数据不足之处"),
    ("环比", "比上一周期"),
    ("同比", "比去年同期"),
)


def _plain_language(value: str) -> str:
    for term, replacement in PLAIN_LANGUAGE_TERMS:
        value = value.replace(term, replacement)
    return value


def _duration(value: Decimal | int | float) -> str:
    seconds = max(0, int(round(float(value or 0))))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _number(value: Decimal | int | float, digits: int = 1) -> str:
    return f"{float(value or 0):,.{digits}f}"


def _change(current: Decimal | int | float, previous: Decimal | int | float) -> float | None:
    previous_value = float(previous or 0)
    if previous_value == 0:
        return None
    return (float(current or 0) - previous_value) / previous_value * 100


def _change_label(value: float | None) -> str:
    if value is None:
        return "无法比较"
    if value > 0:
        return f"上升 {value:.1f}%"
    if value < 0:
        return f"下降 {abs(value):.1f}%"
    return "持平 0.0%"


def _fact(identifier: str, label: str, value: str, source: str) -> dict[str, str]:
    return {"id": identifier, "label": label, "value": value, "source": source}


def _table(name: str, rows: list[dict]) -> dict:
    return {"name": name, "row_count": len(rows), "rows": rows}


def _round_context_numbers(value):
    if isinstance(value, dict):
        return {key: _round_context_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_context_numbers(item) for item in value]
    if isinstance(value, float):
        return round(value, 1)
    return value


def normalize_analysis_context(context: dict) -> tuple[dict, str]:
    """Return the exact model input and its stable, business-data-only version."""
    normalized = _round_context_numbers(to_primitive(context))
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return normalized, canonical


def generate_data_version(context: dict) -> str:
    _, canonical = normalize_analysis_context(context)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _project_distribution(records: list[WorkshopRecord] | list[EquipmentRecord]) -> list[dict]:
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for row in records:
        name = row.project_code or row.ship_no or "未标注工程"
        bucket = buckets.setdefault(
            name, {"task_count": 0, "cut_length": Decimal("0"), "work_time": Decimal("0")}
        )
        bucket["task_count"] = int(bucket["task_count"]) + 1
        bucket["cut_length"] = Decimal(bucket["cut_length"]) + (row.cut_length or Decimal("0"))
        bucket["work_time"] = Decimal(bucket["work_time"]) + (row.work_time or Decimal("0"))
    return [
        {
            "project_name": name,
            "task_count": int(values["task_count"]),
            "cut_length": float(values["cut_length"]),
            "work_time": float(values["work_time"]),
        }
        for name, values in sorted(
            buckets.items(), key=lambda item: (-int(item[1]["task_count"]), item[0])
        )
    ]


def _work_time_outliers(
    records: list[WorkshopRecord] | list[EquipmentRecord],
) -> list[dict]:
    valid = [row for row in records if row.work_time is not None and row.work_time > 0]
    if len(valid) < 4:
        return []
    values = [float(row.work_time or 0) for row in valid]
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    threshold = q3 + 1.5 * (q3 - q1)
    if threshold <= q3:
        threshold = q3 * 2
    return [
        {
            "task_id": row.task_id,
            "program_no": getattr(row, "program_no", None),
            "project_name": row.project_code or row.ship_no or "未标注工程",
            "work_time": float(row.work_time or 0),
            "threshold": round(threshold, 1),
        }
        for row in sorted(valid, key=lambda item: item.work_time or 0, reverse=True)
        if float(row.work_time or 0) > threshold
    ][:5]
