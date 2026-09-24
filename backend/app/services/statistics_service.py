from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.models.domain import (
    AttentionItem,
    StatisticsResult,
    SummaryBaseRecord,
    SummaryMetrics,
    WorkshopRecord,
)


NUMERIC_FIELDS = ("cut_length", "groove_length", "work_time")


def _valid_values(records: list[SummaryBaseRecord], field: str) -> list[Decimal]:
    return [
        value
        for record in records
        if (value := getattr(record, field)) is not None and value >= 0
    ]


def _average(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal("0")) / Decimal(len(values))


def build_worker_work_summaries(
    records: list[WorkshopRecord],
) -> list[dict[str, Any]]:
    """Aggregate confirmed production tasks by their current worker assignment."""
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        if not record.confirmed_by_id:
            continue
        bucket = buckets.setdefault(
            record.confirmed_by_id,
            {
                "worker_id": record.confirmed_by_id,
                "company_no": record.confirmed_by_company_no,
                "worker_name": record.confirmed_by or "--",
                "confirmed_task_count": 0,
                "total_cut_length": Decimal("0"),
                "total_work_time_seconds": Decimal("0"),
                "last_confirmed_at": None,
            },
        )
        if not bucket["company_no"] and record.confirmed_by_company_no:
            bucket["company_no"] = record.confirmed_by_company_no
        bucket["confirmed_task_count"] += 1
        if record.cut_length is not None and record.cut_length >= 0:
            bucket["total_cut_length"] += record.cut_length
        if record.work_time is not None and record.work_time >= 0:
            bucket["total_work_time_seconds"] += record.work_time
        if record.confirmed_at and (
            bucket["last_confirmed_at"] is None
            or record.confirmed_at > bucket["last_confirmed_at"]
        ):
            bucket["last_confirmed_at"] = record.confirmed_at

    total_work_time = sum(
        (item["total_work_time_seconds"] for item in buckets.values()),
        Decimal("0"),
    )
    summaries = []
    for item in buckets.values():
        share = (
            round(float(item["total_work_time_seconds"] / total_work_time * 100), 1)
            if total_work_time > 0
            else 0.0
        )
        summaries.append({**item, "work_time_share": share})
    return sorted(
        summaries,
        key=lambda item: (
            -item["total_work_time_seconds"],
            item["company_no"] or "",
            item["worker_name"],
        ),
    )


def build_workshop_task_distributions(
    records: list[WorkshopRecord],
) -> dict[str, list[dict[str, Any]]]:
    """Build truthful task-duration and confirmation buckets for workshop charts."""
    duration_buckets = [
        {"key": "under_1m", "label": "< 1 分钟", "count": 0},
        {"key": "1_to_5m", "label": "1–5 分钟", "count": 0},
        {"key": "5_to_30m", "label": "5–30 分钟", "count": 0},
        {"key": "over_30m", "label": "> 30 分钟", "count": 0},
    ]
    unknown_count = 0
    confirmed_count = 0
    for record in records:
        if record.confirmed_by:
            confirmed_count += 1
        value = record.work_time
        if value is None or value < 0:
            unknown_count += 1
        elif value < 60:
            duration_buckets[0]["count"] += 1
        elif value < 300:
            duration_buckets[1]["count"] += 1
        elif value <= 1800:
            duration_buckets[2]["count"] += 1
        else:
            duration_buckets[3]["count"] += 1
    if unknown_count:
        duration_buckets.append(
            {"key": "unknown", "label": "暂无时长", "count": unknown_count}
        )
    return {
        "duration_distribution": duration_buckets,
        "status_distribution": [
            {"key": "confirmed", "label": "已确认", "count": confirmed_count},
            {
                "key": "pending",
                "label": "待确认",
                "count": len(records) - confirmed_count,
            },
        ],
    }


class StatisticsService:
    def calculate(self, records: list[SummaryBaseRecord]) -> StatisticsResult:
        quality: list[AttentionItem] = []
        unique_records: dict[str, SummaryBaseRecord] = {}
        for record in records:
            if not record.task_id:
                quality.append(
                    AttentionItem("error", "data_quality", None, "发现任务 ID 缺失记录")
                )
                continue
            if record.task_id in unique_records:
                quality.append(
                    AttentionItem(
                        "error",
                        "data_quality",
                        record.task_id,
                        "汇总基础数据中发现重复任务，重复项未计入统计",
                    )
                )
                continue
            invalid_fields = [
                field
                for field in NUMERIC_FIELDS
                if (value := getattr(record, field)) is not None and value < 0
            ]
            if invalid_fields:
                quality.append(
                    AttentionItem(
                        "error",
                        "data_quality",
                        record.task_id,
                        f"发现负数数据：{', '.join(invalid_fields)}；对应字段不计入统计",
                    )
                )
            unique_records[record.task_id] = record

        valid_records = list(unique_records.values())
        cut_values = _valid_values(valid_records, "cut_length")
        groove_values = _valid_values(valid_records, "groove_length")
        work_values = _valid_values(valid_records, "work_time")
        summary = SummaryMetrics(
            task_count=len(valid_records),
            total_cut_length=sum(cut_values, Decimal("0")),
            total_groove_length=sum(groove_values, Decimal("0")),
            total_work_time=sum(work_values, Decimal("0")),
            avg_cut_length=_average(cut_values),
            avg_groove_length=_average(groove_values),
            avg_work_time=_average(work_values),
            max_cut_length=max(cut_values, default=None),
            min_cut_length=min(cut_values, default=None),
            max_groove_length=max(groove_values, default=None),
            min_groove_length=min(groove_values, default=None),
            max_work_time=max(work_values, default=None),
            min_work_time=min(work_values, default=None),
        )

        attention: list[AttentionItem] = []
        for record in sorted(
            valid_records,
            key=lambda item: item.work_time if item.work_time is not None else Decimal("-1"),
            reverse=True,
        )[:5]:
            if record.work_time is not None:
                attention.append(
                    AttentionItem(
                        "info",
                        "top_work_time",
                        record.task_id,
                        f"任务工时较高：{record.work_time}",
                    )
                )
        return StatisticsResult(summary, valid_records, quality, attention)
