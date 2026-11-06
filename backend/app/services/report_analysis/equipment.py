"""Equipment report facts, task energy, coverage and quality context."""
from __future__ import annotations

from decimal import Decimal

from app.models.domain import EquipmentRecord
from app.services.report_analysis.common import (
    _change,
    _change_label,
    _duration,
    _fact,
    _number,
    _project_distribution,
    _table,
    _work_time_outliers,
)


def _equipment_task_rows(records: list[EquipmentRecord]) -> list[dict]:
    return [
        {
            "task_id": row.task_id,
            "equipment_id": row.equipment_id,
            "project_code": row.project_code,
            "ship_no": row.ship_no,
            "segment_name": row.segment_name,
            "production_time": row.production_time,
            "production_date": row.production_date,
            "work_time": row.work_time,
            "electricity_consumption": row.electricity_consumption,
            "gas_consumption": row.gas_consumption,
            "electricity_data_status": row.electricity_data_status,
            "gas_data_status": row.gas_data_status,
        }
        for row in records
    ]


def _equipment_breakdown(
    records: list[EquipmentRecord],
    *,
    dimension: str,
) -> list[dict]:
    buckets: dict[str, dict[str, Decimal | int]] = {}
    for row in records:
        if dimension == "equipment":
            name = row.equipment_id or "未标注设备"
        else:
            name = row.project_code or row.ship_no or "未标注工程"
        bucket = buckets.setdefault(
            name,
            {
                "task_count": 0,
                "work_time": Decimal("0"),
                "gas": Decimal("0"),
                "electricity": Decimal("0"),
                "gas_count": 0,
                "electricity_count": 0,
            },
        )
        bucket["task_count"] = int(bucket["task_count"]) + 1
        bucket["work_time"] = Decimal(bucket["work_time"]) + (
            row.work_time or Decimal("0")
        )
        if row.gas_consumption is not None:
            bucket["gas"] = Decimal(bucket["gas"]) + row.gas_consumption
            bucket["gas_count"] = int(bucket["gas_count"]) + 1
        if row.electricity_consumption is not None:
            bucket["electricity"] = (
                Decimal(bucket["electricity"]) + row.electricity_consumption
            )
            bucket["electricity_count"] = int(bucket["electricity_count"]) + 1
    return [
        {
            f"{dimension}_name": name,
            "task_count": int(values["task_count"]),
            "work_time": float(values["work_time"]),
            "gas_consumption": float(values["gas"]),
            "electricity_consumption": float(values["electricity"]),
            "gas_record_count": int(values["gas_count"]),
            "electricity_record_count": int(values["electricity_count"]),
        }
        for name, values in sorted(
            buckets.items(),
            key=lambda item: (-int(item[1]["task_count"]), item[0]),
        )
    ]


def build_context(
    report_date: str,
    records: list[EquipmentRecord],
    *,
    energy_data_mode: str = "unavailable",
    energy_units: dict[str, str] | None = None,
    energy_notice: str = "",
    energy_summary_rows: list[dict] | None = None,
    comparison_records: list[EquipmentRecord] | None = None,
    comparison_date: str | None = None,
    year_ago_records: list[EquipmentRecord] | None = None,
    year_ago_date: str | None = None,
) -> dict:
    total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
    total_gas = sum((row.gas_consumption or Decimal("0") for row in records), Decimal("0"))
    total_electricity = sum((row.electricity_consumption or Decimal("0") for row in records), Decimal("0"))
    units = energy_units or ({"gas": "Nm³", "electricity": "kWh"} if energy_data_mode == "mock" else {"gas": "单位待确认", "electricity": "单位待确认"})
    gas_count = sum(row.gas_consumption is not None for row in records)
    electric_count = sum(row.electricity_consumption is not None for row in records)
    gas_estimates = sum(row.gas_data_status == "interpolated" for row in records)
    electric_estimates = sum(row.electricity_data_status == "interpolated" for row in records)
    overlap_count = sum(
        "overlap" in {row.gas_data_status, row.electricity_data_status}
        for row in records
    )
    invalid_interval_count = sum(
        "invalid_interval" in {row.gas_data_status, row.electricity_data_status}
        for row in records
    )
    sampling_issue_statuses = {
        "missing", "gap", "counter_reset", "invalid_reading", "unavailable"
    }
    sampling_issue_count = sum(
        bool(
            {row.gas_data_status, row.electricity_data_status}
            & sampling_issue_statuses
        )
        for row in records
    )

    def energy_fact(total, count, unit):
        if not count:
            return "缺失"
        return f"{_number(total)} {unit}"
    projects = _project_distribution(records)
    anomalies = _work_time_outliers(records)
    previous_count = len(comparison_records) if comparison_records is not None else None
    task_change = _change(len(records), previous_count) if previous_count is not None else None
    year_ago_count = len(year_ago_records) if year_ago_records is not None else None
    year_change = _change(len(records), year_ago_count) if year_ago_count is not None else None
    quality_statuses = sampling_issue_statuses | {"overlap", "invalid_interval", "unconfigured", "unmapped"}
    quality_count = sum(
        row.gas_consumption is None
        or row.electricity_consumption is None
        or bool({row.gas_data_status, row.electricity_data_status} & quality_statuses)
        for row in records
    )
    energy_mode_label = {
        "metered": "实测",
        "partial": "实际仪表数据",
        "mock": "模拟",
        "unavailable": "不可用",
    }.get(energy_data_mode, "未知")

    def energy_source(channel: str, count: int) -> str:
        if not count:
            return f"无有效{channel}记录"
        coverage = "（仅汇总有记录的任务）" if energy_data_mode == "partial" and count < len(records) else ""
        return f"数据来源 {energy_mode_label}{coverage}"
    task_trend_evidence = [f"当前 {len(records)} 项"]
    if previous_count is not None:
        task_trend_evidence.append(
            f"{comparison_date or '上一可比周期'} {previous_count} 项；"
            f"任务数环比 {_change_label(task_change)}"
        )
    else:
        task_trend_evidence.append("未取得上一可比周期数据，环比无法比较")
    if year_ago_count is not None:
        task_trend_evidence.append(
            f"{year_ago_date or '上年同期'} {year_ago_count} 项；"
            f"任务数同比 {_change_label(year_change)}"
        )
    else:
        task_trend_evidence.append("未取得上年同期数据，同比无法比较")
    anomaly_details = (
        "、".join(
            f"任务 {item['task_id']} 工时 {_duration(item['work_time'])}"
            for item in anomalies
        )
        if anomalies
        else "0 项"
    )
    facts = [
        _fact(
            "F001",
            "任务总数",
            f"{len(records)} 项",
            f"设备报表周期 {report_date}；共 {len(records)} 项任务",
        ),
        _fact(
            "F002",
            "总切割时长",
            _duration(total_work),
            f"设备报表周期 {report_date}；任务切割时长合计 {_duration(total_work)}",
        ),
        _fact(
            "F003",
            "已匹配任务气耗",
            energy_fact(total_gas, gas_count, units["gas"]),
            f"{energy_source('气耗', gas_count)}；有气耗记录 {gas_count} 项，共 {len(records)} 项；已记录任务气耗合计 "
            f"{energy_fact(total_gas, gas_count, units['gas'])}；根据前后读数估算 {gas_estimates} 项",
        ),
        _fact(
            "F004",
            "已匹配任务电耗",
            energy_fact(total_electricity, electric_count, units["electricity"]),
            f"{energy_source('电耗', electric_count)}；有电耗记录 {electric_count} 项，共 {len(records)} 项；已记录任务电耗合计 "
            f"{energy_fact(total_electricity, electric_count, units['electricity'])}；根据前后读数估算 {electric_estimates} 项",
        ),
        _fact(
            "F005",
            "任务变化与切割时间",
            f"比上一周期{_change_label(task_change)}，{len(anomalies)} 项切割时间较长",
            "；".join(task_trend_evidence)
            + f"；按切割时间分布筛出的较长任务共 {len(anomalies)} 项，最多展示 5 项：{anomaly_details}",
        ),
        _fact(
            "F006",
            "能耗数据待核对任务",
            f"{quality_count} 项",
            f"气耗或电耗缺失或异常的任务共 {quality_count} 项，同一任务只计一次；"
            f"有气耗记录 {gas_count}/{len(records)} 项，有电耗记录 {electric_count}/{len(records)} 项；"
            f"仪表记录时间重叠 {overlap_count} 项，仪表记录不完整 {sampling_issue_count} 项，"
            f"任务起止时间有问题 {invalid_interval_count} 项；另含未设置仪表或未关联设备的记录",
        ),
    ]
    context = {
        "facts": facts,
        "report_date": report_date,
        "tables": [
            _table("任务能耗明细", _equipment_task_rows(records)),
            _table("近12个月月度能耗汇总", energy_summary_rows or []),
        ],
        "task_count": len(records),
        "energy_data_mode": energy_data_mode,
        "energy_notice": energy_notice,
        "anomaly_count": len(anomalies),
        "quality_count": quality_count,
        "energy_coverage": {"gas": gas_count, "electricity": electric_count, "tasks": len(records)},
        "energy_estimates": {"gas": gas_estimates, "electricity": electric_estimates},
        "energy_quality": {
            "overlap": overlap_count,
            "sampling_issue": sampling_issue_count,
            "invalid_interval": invalid_interval_count,
        },
        "comparisons": {
            "comparison_date": comparison_date,
            "task_change_percent": round(task_change, 1) if task_change is not None else None,
            "year_ago_date": year_ago_date,
            "task_yoy_percent": round(year_change, 1) if year_change is not None else None,
        },
        "project_distribution": projects[:10],
        "energy_by_project": _equipment_breakdown(
            records, dimension="project"
        )[:10],
        "equipment_distribution": _equipment_breakdown(
            records, dimension="equipment"
        )[:10],
        "anomalies": anomalies,
    }
    return context
