from __future__ import annotations

from app.models.domain import (
    EquipmentRecord,
    LeadershipReportResult,
    SummaryBaseRecord,
)


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
                    "data": [float(item.cut_length or 0) for item in records],
                    "itemStyle": {"color": "#1769aa"},
                },
                {
                    "name": "坡口长度",
                    "type": "bar",
                    "data": [float(item.groove_length or 0) for item in records],
                    "itemStyle": {"color": "#56a3a6"},
                },
                {
                    "name": "切割时长（分钟）",
                    "type": "line",
                    "yAxisIndex": 1,
                    "smooth": True,
                    "data": [round(float(item.work_time or 0) / 60, 2) for item in records],
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
                        float(item.gas_consumption or 0)
                        for item in equipment_detail_records
                    ],
                    "itemStyle": {"color": "#6c8ebf"},
                },
                {
                    "name": "电耗",
                    "type": "bar",
                    "data": [
                        float(item.electricity_consumption or 0)
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
                        round(float(item.work_time or 0) / 60, 2)
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
                    "data": [float(item.cut_length_m) for item in daily],
                    "itemStyle": {"color": "#1769aa"},
                },
                {
                    "name": "坡口长度",
                    "type": "bar",
                    "data": [float(item.groove_length_m) for item in daily],
                    "itemStyle": {"color": "#56a3a6"},
                },
                {
                    "name": "切割工时",
                    "type": "line",
                    "yAxisIndex": 1,
                    "smooth": True,
                    "data": [round(item.work_time_seconds / 3600, 2) for item in daily],
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
                    "data": [float(item.cut_length_m) for item in monthly],
                    "itemStyle": {"color": "#1769aa"},
                },
                {
                    "name": "坡口长度",
                    "type": "bar",
                    "data": [float(item.groove_length_m) for item in monthly],
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
