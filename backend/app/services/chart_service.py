from __future__ import annotations

from app.models.domain import (
    EquipmentRecord,
    LeadershipReportResult,
    SummaryBaseRecord,
)


def _chart_number(value) -> float | None:
    if value is None:
        return None
    return round(float(value), 1)


class ChartService:
    def __init__(self, top_n: int = 20) -> None:
        self.top_n = top_n

    @staticmethod
    def _apply_chart_type(option: dict, chart_type: str) -> dict:
        normalized = chart_type if chart_type in {"bar", "line", "area", "bar_line"} else "bar_line"
        if normalized == "bar_line":
            return option
        for series in option.get("series", []):
            series.pop("areaStyle", None)
            if normalized == "bar":
                series["type"] = "bar"
                series.pop("smooth", None)
            else:
                series["type"] = "line"
                series["smooth"] = True
                if normalized == "area":
                    series["areaStyle"] = {"opacity": 0.16}
        return option

    def build(
        self,
        summary_records: list[SummaryBaseRecord],
        equipment_records: list[EquipmentRecord],
        chart_type: str = "bar_line",
    ) -> dict[str, dict]:
        records = sorted(
            summary_records,
            key=lambda item: item.cut_length or 0,
            reverse=True,
        )[: self.top_n]
        labels = [item.program_no or item.task_id for item in records]
        length_combo = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["切割长度", "坡口长度", "切割时长（分钟）"]},
            "grid": {"left": 58, "right": 62, "bottom": 76, "top": 52},
            "xAxis": {
                "type": "category",
                "data": labels,
                "axisLabel": {"rotate": 35},
            },
            "yAxis": [
                {"type": "value", "name": "长度"},
                {"type": "value", "name": "时长（分钟）"},
            ],
            "series": [
                {
                    "name": "切割长度",
                    "type": "bar",
                    "data": [_chart_number(item.cut_length or 0) for item in records],
                    "itemStyle": {"color": "#1769aa"},
                },
                {
                    "name": "坡口长度",
                    "type": "bar",
                    "data": [_chart_number(item.groove_length or 0) for item in records],
                    "itemStyle": {"color": "#56a3a6"},
                },
                {
                    "name": "切割时长（分钟）",
                    "type": "line",
                    "yAxisIndex": 1,
                    "smooth": True,
                    "data": [round(float(item.work_time or 0) / 60, 1) for item in records],
                    "itemStyle": {"color": "#e58e26"},
                },
            ],
        }
        equipment_detail_records = sorted(
            equipment_records,
            key=lambda item: (
                item.production_time is None,
                item.production_time.isoformat() if item.production_time else "",
                item.task_id,
            ),
        )[: self.top_n]
        equipment_detail = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["气耗", "电耗", "切割时长（分钟）"]},
            "grid": {"left": 58, "right": 62, "bottom": 66, "top": 52},
            "xAxis": {
                "type": "category",
                "name": "开始时间",
                "data": [
                    item.production_time.strftime("%H:%M")
                    if item.production_time
                    else "--"
                    for item in equipment_detail_records
                ],
                "axisLabel": {"rotate": 30},
            },
            "yAxis": [
                {"type": "value", "name": "气耗 / 电耗"},
                {"type": "value", "name": "切割时长（分钟）"},
            ],
            "series": [
                {
                    "name": "气耗",
                    "type": "bar",
                    "data": [
                        _chart_number(item.gas_consumption)
                        for item in equipment_detail_records
                    ],
                    "itemStyle": {"color": "#6c8ebf"},
                },
                {
                    "name": "电耗",
                    "type": "bar",
                    "data": [
                        _chart_number(item.electricity_consumption)
                        for item in equipment_detail_records
                    ],
                    "itemStyle": {"color": "#d79b00"},
                },
                {
                    "name": "切割时长（分钟）",
                    "type": "line",
                    "yAxisIndex": 1,
                    "smooth": True,
                    "data": [
                        round(float(item.work_time or 0) / 60, 1)
                        for item in equipment_detail_records
                    ],
                    "itemStyle": {"color": "#e58e26"},
                },
            ],
        }
        return {
            "length_combo": self._apply_chart_type(length_combo, chart_type),
            "equipment_detail": self._apply_chart_type(
                equipment_detail, chart_type
            ),
        }

    def build_energy_period(
        self,
        task_report: dict,
        month_report: dict,
        chart_type: str = "bar_line",
    ) -> dict[str, dict]:
        units = task_report.get("units") or {"electricity": "kWh", "gas": "Nm³"}
        task_rows = task_report.get("records") or []
        month_rows = month_report.get("rows") or []

        def option(labels, electricity, gas):
            return {
                "tooltip": {"trigger": "axis", "confine": True},
                "legend": {"data": ["电耗", "气耗"], "top": 6},
                "grid": {"top": 70, "bottom": 58, "left": 65, "right": 65},
                "xAxis": {
                    "type": "category",
                    "data": labels,
                    "axisLabel": {"hideOverlap": True, "rotate": 28},
                },
                "yAxis": [
                    {"type": "value", "name": f"电耗 {units['electricity']}"},
                    {"type": "value", "name": f"气耗 {units['gas']}"},
                ],
                "series": [
                    {
                        "name": "电耗",
                        "type": "bar",
                        "yAxisIndex": 0,
                        "data": electricity,
                        "itemStyle": {"color": "#db8c32"},
                    },
                    {
                        "name": "气耗",
                        "type": "line",
                        "yAxisIndex": 1,
                        "data": gas,
                        "connectNulls": False,
                        "itemStyle": {"color": "#167e85"},
                    },
                ],
            }

        task_labels = []
        for index, row in enumerate(task_rows):
            start = str(row.get("production_time") or "")
            clock = start[11:16] if len(start) >= 16 else ""
            segment = row.get("segment_name") or row.get("project_code") or f"任务{index + 1}"
            task_labels.append(f"{clock} {segment}".strip())

        task = option(
            task_labels,
            [_chart_number(row.get("electricity_consumption")) for row in task_rows],
            [_chart_number(row.get("gas_consumption")) for row in task_rows],
        )
        monthly = option(
            [str(row.get("report_date") or "")[-2:] for row in month_rows],
            [_chart_number((row.get("electricity") or {}).get("value")) for row in month_rows],
            [_chart_number((row.get("gas") or {}).get("value")) for row in month_rows],
        )
        return {
            "equipment_detail": self._apply_chart_type(task, chart_type),
            "equipment_month": self._apply_chart_type(monthly, chart_type),
        }

    def build_leadership(
        self, report: LeadershipReportResult, chart_type: str = "bar_line"
    ) -> dict[str, dict]:
        # 表格保持最近日期优先；图表按时间从左到右递增。
        daily = sorted(report.daily_records, key=lambda item: item.period_start)
        monthly = sorted(report.monthly_records, key=lambda item: item.period_start)
        daily_trend = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["切割长度", "坡口长度", "切割工时"]},
            "grid": {"left": 62, "right": 70, "bottom": 62, "top": 52},
            "xAxis": {
                "type": "category",
                "data": [item.period_start.strftime("%m-%d") for item in daily],
            },
            "yAxis": [
                {"type": "value", "name": "长度（米）"},
                {"type": "value", "name": "工时（小时）"},
            ],
            "series": [
                {
                    "name": "切割长度",
                    "type": "bar",
                    "data": [_chart_number(item.cut_length_m) for item in daily],
                    "itemStyle": {"color": "#1769aa"},
                },
                {
                    "name": "坡口长度",
                    "type": "bar",
                    "data": [_chart_number(item.groove_length_m) for item in daily],
                    "itemStyle": {"color": "#56a3a6"},
                },
                {
                    "name": "切割工时",
                    "type": "line",
                    "yAxisIndex": 1,
                    "smooth": True,
                    "data": [round(item.work_time_seconds / 3600, 1) for item in daily],
                    "itemStyle": {"color": "#e58e26"},
                },
            ],
        }
        monthly_trend = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": ["切割长度", "坡口长度", "完成任务"]},
            "grid": {"left": 62, "right": 70, "bottom": 62, "top": 52},
            "xAxis": {
                "type": "category",
                "data": [item.period_start.strftime("%Y-%m") for item in monthly],
            },
            "yAxis": [
                {"type": "value", "name": "长度（米）"},
                {"type": "value", "name": "任务数"},
            ],
            "series": [
                {
                    "name": "切割长度",
                    "type": "bar",
                    "data": [_chart_number(item.cut_length_m) for item in monthly],
                    "itemStyle": {"color": "#1769aa"},
                },
                {
                    "name": "坡口长度",
                    "type": "bar",
                    "data": [_chart_number(item.groove_length_m) for item in monthly],
                    "itemStyle": {"color": "#56a3a6"},
                },
                {
                    "name": "完成任务",
                    "type": "line",
                    "yAxisIndex": 1,
                    "smooth": True,
                    "data": [item.task_count for item in monthly],
                    "itemStyle": {"color": "#d47f22"},
                },
            ],
        }
        return {
            "daily_trend": self._apply_chart_type(daily_trend, chart_type),
            "monthly_trend": self._apply_chart_type(monthly_trend, chart_type),
        }
