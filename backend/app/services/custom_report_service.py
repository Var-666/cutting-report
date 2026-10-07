from __future__ import annotations

import calendar
import re
import math
from contextlib import closing
from itertools import islice
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Iterator

from app.config import Settings
from app.database.sqlserver import SqlServerClient
from app.database.excel_task import ExcelTaskDataSource
from app.services.sql_workbench_service import SqlWorkbenchService
from app.services.report_deadline import ensure_time_remaining
from sqlglot import transpile


PARAMETER_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")


def _primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class CustomReportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = SqlServerClient(settings)
        self.excel = ExcelTaskDataSource(settings.task_excel_path)
        self.validator = SqlWorkbenchService(settings)

    def validate(self, sql_text: str) -> dict[str, Any]:
        validated = self.validator.validate(sql_text)
        parameters = PARAMETER_PATTERN.findall(validated.formatted_sql)
        return {
            "formatted_sql": validated.formatted_sql,
            "tables": validated.tables,
            "columns": validated.columns,
            "parameters": list(dict.fromkeys(parameters)),
        }

    @staticmethod
    def _period_end(report_date: date, period_type: str) -> date:
        if period_type == "month":
            days = calendar.monthrange(report_date.year, report_date.month)[1]
            return report_date.replace(day=days) + timedelta(days=1)
        return report_date + timedelta(days=1)

    def _compile(
        self,
        sql_text: str,
        *,
        report_date: date,
        period_type: str,
        project_code: str | None,
        ship_no: str | None,
        parameters: dict[str, Any],
    ) -> tuple[str, tuple[Any, ...]]:
        validated = self.validator.validate(sql_text)
        available = {
            "report_date": report_date,
            "period_start": report_date,
            "period_end": self._period_end(report_date, period_type),
            "project_code": project_code,
            "ship_no": ship_no,
            **parameters,
        }
        values: list[Any] = []

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in available:
                raise ValueError(f"查询参数 :{name} 没有提供值")
            values.append(available[name])
            return "?"

        return PARAMETER_PATTERN.sub(replace, validated.formatted_sql), tuple(values)

    @staticmethod
    def _demo_rows(columns: list[dict[str, Any]], report_date: date, max_rows: int) -> list[dict[str, Any]]:
        rows = []
        count = min(max_rows, 10)
        for index in range(count):
            row: dict[str, Any] = {}
            for column in columns:
                field = column["field"]
                field_type = column.get("type", "text")
                if field_type == "number":
                    row[field] = round((index + 1) * 128.6, 1)
                elif field_type == "date":
                    row[field] = (report_date - timedelta(days=index)).isoformat()
                elif field_type == "datetime":
                    row[field] = f"{report_date.isoformat()}T{8 + index:02d}:00:00"
                elif field_type == "duration":
                    row[field] = (index + 1) * 1800
                else:
                    row[field] = f"示例{index + 1}"
            rows.append(row)
        return rows

    def execute(
        self,
        query_task: dict[str, Any],
        *,
        report_date: date,
        period_type: str,
        project_code: str | None = None,
        ship_no: str | None = None,
        parameters: dict[str, Any] | None = None,
        max_rows: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = min(max_rows or self.settings.report_max_detail_rows, self.settings.report_max_detail_rows)
        with closing(self.iter_rows(
            query_task, report_date=report_date, period_type=period_type,
            project_code=project_code, ship_no=ship_no, parameters=parameters,
        )) as rows:
            return list(islice(rows, limit))

    def iter_rows(
        self,
        query_task: dict[str, Any],
        *,
        report_date: date,
        period_type: str,
        project_code: str | None = None,
        ship_no: str | None = None,
        parameters: dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        ensure_time_remaining()
        if self.settings.effective_data_source_mode == "demo":
            yield from self._demo_rows(query_task["columns"], report_date, 10)
            return
        sql, values = self._compile(
            query_task["sql_text"], report_date=report_date, period_type=period_type,
            project_code=project_code, ship_no=ship_no, parameters=parameters or {},
        )
        if self.settings.effective_data_source_mode == "excel":
            sqlite_sql = transpile(sql, read="tsql", write="sqlite")[0]
            source = self.excel.iter_select(sqlite_sql, values)
        else:
            source = self.client.iter_rows(sql, values)
        with closing(source):
            for index, row in enumerate(source, start=1):
                if index == 1 or index % 128 == 0:
                    ensure_time_remaining()
                yield {key: _primitive(value) for key, value in row.items()}

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            number = float(value) if value not in (None, "") else None
            return number if number is not None and math.isfinite(number) else None
        except (TypeError, ValueError, OverflowError):
            return None

    def build_report_data(
        self,
        *,
        definition: dict[str, Any],
        query_task: dict[str, Any],
        template: dict[str, Any],
        rows: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        config = definition.get("visual_config") or {}
        column_map = {column["field"]: column for column in query_task["columns"]}
        selected_fields = config.get("columns") or list(column_map)
        columns = [column_map[field] for field in selected_fields if field in column_map]
        metric_configs = config.get("metrics") or []
        totals = [{"sum": 0.0, "count": 0, "min": None, "max": None} for _ in metric_configs]
        displayed_rows: list[dict[str, Any]] = []
        total_row_count = 0
        ensure_time_remaining()
        for row in rows:
            total_row_count += 1
            if total_row_count % 128 == 0:
                ensure_time_remaining()
            if len(displayed_rows) < self.settings.report_max_detail_rows:
                displayed_rows.append(row)
            for metric, total in zip(metric_configs, totals, strict=True):
                number = self._number(row.get(metric["field"]))
                if number is None:
                    continue
                total["sum"] += number
                total["count"] += 1
                total["min"] = number if total["min"] is None else min(total["min"], number)
                total["max"] = number if total["max"] is None else max(total["max"], number)
        metrics = []
        for metric, total in zip(metric_configs, totals, strict=True):
            aggregation = metric.get("aggregation", "sum")
            if aggregation == "count":
                value = total_row_count
            elif not total["count"]:
                value = 0
            elif aggregation == "avg":
                value = total["sum"] / total["count"]
            elif aggregation in {"min", "max"}:
                value = total[aggregation]
            else:
                value = total["sum"]
            metrics.append({**metric, "value": round(value, 1) if isinstance(value, float) else value})
        rows = displayed_rows

        chart_config = config.get("chart") or {}
        category_field = chart_config.get("category_field")
        value_fields = [field for field in chart_config.get("value_fields", []) if field in column_map]
        chart_type = chart_config.get("type", "bar")
        series = []
        colors = [template["config"]["primary_color"], template["config"]["accent_color"], "#d47f22", "#6c8ebf"]
        for index, field in enumerate(value_fields):
            series_type = "line" if chart_type in {"line", "area"} or (chart_type == "bar_line" and index > 0) else "bar"
            item = {
                    "name": column_map[field]["label"],
                    "type": series_type,
                    "smooth": series_type == "line",
                    "data": [round(self._number(row.get(field)) or 0, 1) for row in rows],
                    "itemStyle": {"color": colors[index % len(colors)]},
            }
            if chart_type == "area":
                item["areaStyle"] = {"opacity": 0.16}
            series.append(item)
        chart = {
            "tooltip": {"trigger": "axis"},
            "legend": {"data": [item["name"] for item in series]},
            "grid": {"left": 62, "right": 28, "bottom": 58, "top": 52},
            "xAxis": {
                "type": "category",
                "data": [str(row.get(category_field, index + 1)) for index, row in enumerate(rows)],
                "axisLabel": {"rotate": 25 if len(rows) > 8 else 0},
            },
            "yAxis": {"type": "value"},
            "series": series,
        }
        return {
            "columns": columns,
            "metrics": metrics,
            "rows": rows,
            "total_row_count": total_row_count,
            "truncated": total_row_count > len(rows),
            "chart": chart,
            "chart_title": chart_config.get("title") or "数据趋势",
            "template": template["config"],
        }
