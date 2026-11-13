"""Workshop report facts, task details and worker summaries."""
from __future__ import annotations

from decimal import Decimal

from app.models.domain import WorkshopRecord
from app.services.report_analysis.brief import add_candidates
from app.services.report_analysis.common import (
    _change,
    _change_label,
    _duration,
    _fact,
    _number,
    _plain_language,
    _project_distribution,
    _table,
    _work_time_outliers,
)
from app.services.statistics_service import build_worker_work_summaries


def _workshop_task_rows(records: list[WorkshopRecord]) -> list[dict]:
    return [
        {
            "task_id": row.task_id,
            "project_code": row.project_code,
            "ship_no": row.ship_no,
            "segment_name": row.segment_name,
            "program_no": row.program_no,
            "cut_length": row.cut_length,
            "groove_length": row.groove_length,
            "work_time": row.work_time,
            "production_time": row.production_time,
            "production_date": row.production_date,
            "confirmed_by": row.confirmed_by,
            "confirmed_at": row.confirmed_at,
            "remark": row.remark,
        }
        for row in records
    ]


def _worker_summary_rows(records: list[WorkshopRecord]) -> list[dict]:
    return [
        {
            key: row.get(key)
            for key in (
                "company_no",
                "worker_name",
                "confirmed_task_count",
                "total_cut_length",
                "total_work_time_seconds",
                "work_time_share",
                "last_confirmed_at",
            )
        }
        for row in build_worker_work_summaries(records)
    ]


def build_context(
    report_date: str,
    records: list[WorkshopRecord],
    *,
    comparison_records: list[WorkshopRecord] | None = None,
    comparison_date: str | None = None,
    year_ago_records: list[WorkshopRecord] | None = None,
    year_ago_date: str | None = None,
) -> dict:
    confirmed = sum(1 for row in records if row.confirmed_by)
    total_work = sum((row.work_time or Decimal("0") for row in records), Decimal("0"))
    total_cut = sum((row.cut_length or Decimal("0") for row in records), Decimal("0"))
    projects = _project_distribution(records)
    anomalies = _work_time_outliers(records)
    previous_count = len(comparison_records) if comparison_records is not None else None
    previous_cut = (
        sum((row.cut_length or Decimal("0") for row in comparison_records), Decimal("0"))
        if comparison_records is not None
        else None
    )
    task_change = _change(len(records), previous_count) if previous_count is not None else None
    cut_change = _change(total_cut, previous_cut) if previous_cut is not None else None
    year_ago_count = len(year_ago_records) if year_ago_records is not None else None
    year_change = _change(len(records), year_ago_count) if year_ago_count is not None else None
    unconfirmed = len(records) - confirmed
    primary_project = (
        f"{_plain_language(projects[0]['project_name'])} · {projects[0]['task_count']} 项"
        if projects
        else "暂无工程数据"
    )
    project_note = f"{'工程信息缺口' if projects and projects[0]['project_name'] == '未标注工程' else '任务最多工程'}：{primary_project}"
    pending_share = f"{unconfirmed / len(records) * 100:.1f}%" if records else None
    pending_label = f"待确认 {unconfirmed} 项" + (f"，占全部任务 {pending_share}" if pending_share is not None else '')
    trend_evidence = [pending_label]
    if previous_count is not None:
        trend_evidence.append(
            f"当前 {len(records)} 项；{comparison_date or '上一可比周期'} "
            f"{previous_count} 项；任务数环比 {_change_label(task_change)}；"
            f"切割长度环比 {_change_label(cut_change)}"
        )
    else:
        trend_evidence.append("未取得上一可比周期数据，环比无法比较")
    if year_ago_count is not None:
        trend_evidence.append(
            f"{year_ago_date or '上年同期'} {year_ago_count} 项；"
            f"任务数同比 {_change_label(year_change)}"
        )
    else:
        trend_evidence.append("未取得上年同期数据，同比无法比较")
    anomaly_details = (
        "、".join(
            f"任务 {item['task_id']} 工时 {_duration(item['work_time'])}"
            for item in anomalies[:5]
        )
        if anomalies
        else "0 项"
    )
    facts = [
        _fact(
            "F001",
            "任务总数",
            f"{len(records)} 项",
            f"车间报表周期 {report_date}；共 {len(records)} 项任务",
        ),
        _fact(
            "F002",
            "总切割长度",
            f"{_number(total_cut)} 米",
            f"车间报表周期 {report_date}；任务切割长度合计 {_number(total_cut)} 米",
        ),
        _fact(
            "F003",
            "总切割时长",
            _duration(total_work),
            f"车间报表周期 {report_date}；任务切割时长合计 {_duration(total_work)}",
        ),
        _fact(
            "F004",
            "工人确认率",
            f"{confirmed / len(records) * 100:.1f}%" if records else "暂无任务",
            f"已确认 {confirmed} 项；任务总数 {len(records)} 项；确认率 "
            + (f"{confirmed / len(records) * 100:.1f}%" if records else "暂无任务"),
        ),
        _fact(
            "F005",
            "待确认任务与数量变化",
            f"{pending_label}；任务总数比上一周期{_change_label(task_change)}",
            "；".join(trend_evidence),
        ),
        _fact(
            "F006",
            "切割时间与工程",
            f"{len(anomalies)} 项切割时间较长，{project_note}",
            f"按切割时间分布筛出的较长任务共 {len(anomalies)} 项，最多展示 5 项："
            f"{anomaly_details}；{project_note}",
        ),
    ]
    context = {
        "facts": facts,
        "report_date": report_date,
        "tables": [
            _table("任务明细", _workshop_task_rows(records)),
            _table("工人工时汇总", _worker_summary_rows(records)),
        ],
        "task_count": len(records),
        "confirmation_rate": round(confirmed / len(records) * 100, 1) if records else 0,
        "unconfirmed_count": unconfirmed,
        "anomaly_count": len(anomalies),
        "comparisons": {
            "comparison_date": comparison_date,
            "task_change_percent": round(task_change, 1) if task_change is not None else None,
            "cut_length_change_percent": round(cut_change, 1) if cut_change is not None else None,
            "year_ago_date": year_ago_date,
            "task_yoy_percent": round(year_change, 1) if year_change is not None else None,
        },
        "project_distribution": projects[:10],
        "anomalies": anomalies,
    }
    return add_candidates(context, 'workshop', records)
