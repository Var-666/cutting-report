"""Leadership report facts, comparisons and complete model input tables."""
from __future__ import annotations

import statistics
from calendar import monthrange
from datetime import timedelta

from app.models.domain import LeadershipReportResult, to_primitive
from app.services.report_analysis.brief import add_candidates
from app.services.report_analysis.common import (
    _change,
    _change_label,
    _duration,
    _fact,
    _number,
    _table,
)


def build_context(report: LeadershipReportResult, monthly_records: list) -> dict:
    summary_rows = monthly_records
    current = report.current_month_total
    range_mode = report.period_start is not None and report.period_end is not None
    previous_month = (report.report_month.replace(day=1) - timedelta(days=1)).replace(day=1)
    previous = None
    comparison_start = comparison_end = None
    if range_mode:
        if (report.comparison_total is not None and report.comparison_start and report.comparison_end
            and report.comparison_end == report.period_start - timedelta(days=1)
            and report.comparison_end - report.comparison_start == report.period_end - report.period_start):
            previous = report.comparison_total
            comparison_start, comparison_end = report.comparison_start, report.comparison_end
    else:
        previous = next((r for r in report.monthly_records if r.period_start == previous_month), None)
        if previous is not None:
            comparison_start = previous_month
            comparison_end = previous_month.replace(day=monthrange(previous_month.year, previous_month.month)[1])
    year_ago = report.year_ago_total if (not range_mode and report.year_ago_total
        and report.year_ago_total.period_start == report.report_month.replace(year=report.report_month.year - 1)) else None
    period_label = (
        f"{report.period_start.isoformat()} 至 {report.period_end.isoformat()}"
        if range_mode
        else report.report_month.isoformat()[:7]
    )
    period_name = "区间" if range_mode else "当月"
    month_change = _change(current.task_count, previous.task_count) if previous else None
    year_change = (
        _change(current.task_count, year_ago.task_count)
        if year_ago
        else None
    )
    length_month_change = (
        _change(current.cut_length_m, previous.cut_length_m) if previous else None
    )
    projects = [
        {
            "project_name": item.project_name,
            "task_count": item.task_count,
            "cut_length_m": float(item.cut_length_m),
            "work_time_seconds": item.work_time_seconds,
        }
        for item in report.project_distribution[:10]
    ]
    daily_counts = [item.task_count for item in report.daily_records]
    daily_anomalies: list[dict] = []
    daily_threshold: float | None = None
    if len(daily_counts) >= 5:
        average = statistics.mean(daily_counts)
        deviation = statistics.pstdev(daily_counts)
        daily_threshold = average + 1.5 * deviation
        daily_anomalies = [
            {
                "date": item.period_start.isoformat(),
                "task_count": item.task_count,
                "threshold": round(daily_threshold, 1),
            }
            for item in report.daily_records
            if deviation > 0 and item.task_count > daily_threshold
        ]
    previous_label = (
        f"{comparison_start.isoformat()} 至 {comparison_end.isoformat()}"
        if comparison_start and comparison_end
        else "上一对比周期"
    )
    year_ago_label = (
        report.report_month.replace(year=report.report_month.year - 1).isoformat()[:7]
        if year_ago
        else "上年同期"
    )
    project_value = (
        f"{'工程号未填写' if projects[0]['project_name'] == '未标注工程' else projects[0]['project_name']} "
        f"{projects[0]['task_count']} 项"
        if projects and current.task_count
        else "暂无工程数据"
    )
    anomaly_value = (
        f"{len(daily_anomalies)} 天"
        if len(daily_counts) >= 5
        else "记录太少，暂无法判断"
    )
    anomaly_source = (
        f"每日任务数参考值 {daily_threshold:.1f} 项；任务较多的日期 "
        + (
            "、".join(
                f"{item['date']} {item['task_count']} 项"
                for item in daily_anomalies
            )
            if daily_anomalies
            else "0 天"
        )
        if daily_threshold is not None
        else f"当前仅 {len(daily_counts)} 天有任务记录，少于 5 天，暂无法判断哪些日期任务较多"
    )
    facts = [
        _fact(
            "F001",
            f"{period_name}任务数",
            f"{current.task_count} 项",
            f"报表周期 {period_label}；{period_name}汇总任务 {current.task_count} 项",
        ),
        _fact(
            "F002",
            f"{period_name}切割长度",
            f"{_number(current.cut_length_m)} 米",
            f"报表周期 {period_label}；{period_name}汇总切割长度 {_number(current.cut_length_m)} 米",
        ),
        _fact(
            "F003",
            f"{period_name}切割时长",
            _duration(current.work_time_seconds),
            f"报表周期 {period_label}；{period_name}汇总切割时长 {_duration(current.work_time_seconds)}",
        ),
        _fact(
            "F004",
            "任务数比上一周期",
            _change_label(month_change),
            (
                f"当前 {current.task_count} 项；{previous_label} "
                f"{previous.task_count if previous else 0} 项；任务数比上一可比周期 {_change_label(month_change)}；"
                f"切割长度比上一可比周期 {_change_label(length_month_change)}"
                if previous
                else "未取得上一可比周期数据，任务数和切割长度均无法比较"
            ),
        ),
        _fact(
            "F005",
            "任务数比去年同期",
            _change_label(year_change),
            (
                f"当前 {current.task_count} 项；{year_ago_label} "
                f"{year_ago.task_count} 项；任务数同比 {_change_label(year_change)}"
                if year_ago
                else "未取得上年同期数据，任务数无法同比"
            ),
        ),
        _fact(
            "F006",
            "工程号与任务较多的日期",
            f"{project_value}，任务较多的日期 {anomaly_value}",
            f"任务最多工程：{project_value}；{anomaly_source}",
        ),
    ]
    facts.extend([
        _fact("F007", f"{period_name}坡口长度", f"{_number(current.groove_length_m)} 米", f"报表周期 {period_label}；坡口长度合计"),
        _fact("F008", "上一可比周期的生产汇总",
              f"切割长度 {_number(previous.cut_length_m)} 米；坡口长度 {_number(previous.groove_length_m)} 米；切割时长 {_duration(previous.work_time_seconds)}" if previous else "无法比较",
              previous_label if previous else "未取得明确可比周期数据"),
    ])
    context = {
        "current_task_count": current.task_count,
        "distribution_coverage": {"project_groups": len(report.project_distribution), "shown": len(projects)},
        "data_quality": ["每日分布仅反映有记录的生产日；无记录日期不推断为停机。"],
        "facts": facts,
        "report_month": period_label,
        "tables": [
            _table("区间每日明细", to_primitive(report.daily_records)),
            _table(
                "近12个月月度汇总",
                to_primitive(summary_rows),
            ),
        ],
        "comparisons": {
            "comparison_date": f"{comparison_start.isoformat()} 至 {comparison_end.isoformat()}" if comparison_start else None,
            "groove_change_percent": _change(current.groove_length_m, previous.groove_length_m) if previous else None,
            "work_time_change_percent": _change(current.work_time_seconds, previous.work_time_seconds) if previous else None,
            "task_mom_percent": round(month_change, 1) if month_change is not None else None,
            "cut_length_mom_percent": round(length_month_change, 1) if length_month_change is not None else None,
            "task_yoy_percent": round(year_change, 1) if year_change is not None else None,
        },
        "project_distribution": projects,
        "anomalies": daily_anomalies,
        "daily_sample_count": len(daily_counts),
    }
    return add_candidates(context, 'leadership')
