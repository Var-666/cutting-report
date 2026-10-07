from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings


class ReportRenderError(RuntimeError):
    pass


def format_number(value: Any) -> str:
    if value is None:
        return "--"
    number = float(value)
    return f"{number:,.1f}"


def format_duration(value: Any) -> str:
    if value is None:
        return "--"
    total_seconds = max(0, round(float(value)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _date_parts(value: Any) -> tuple[str, int, int | None] | None:
    match = re.match(r"^(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", str(value or ""))
    if not match:
        return None
    return match.group(1), int(match.group(2)), int(match.group(3)) if match.group(3) else None


def format_chinese_month(value: Any) -> str:
    parts = _date_parts(value)
    return f"{parts[0]}年{parts[1]}月" if parts else "--"


def format_chinese_date(value: Any) -> str:
    parts = _date_parts(value)
    if not parts:
        return "--"
    if parts[2] is None:
        return f"{parts[0]}年{parts[1]}月"
    return f"{parts[0]}年{parts[1]}月{parts[2]}日"


def format_chinese_datetime(value: Any) -> str:
    formatted_date = format_chinese_date(value)
    if formatted_date == "--":
        return formatted_date
    match = re.search(r"[T\s](\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", str(value))
    if not match:
        return formatted_date
    return f"{formatted_date} {int(match.group(1)):02d}:{int(match.group(2)):02d}:{int(match.group(3) or 0):02d}"


class ReportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        template_dir = Path(__file__).resolve().parents[1] / "templates"
        self.environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml", "j2"]),
        )
        self.environment.filters["number"] = format_number
        self.environment.filters["duration"] = format_duration
        self.environment.filters["chinese_month"] = format_chinese_month
        self.environment.filters["chinese_date"] = format_chinese_date
        self.environment.filters["chinese_datetime"] = format_chinese_datetime

    def render(
        self,
        *,
        report_id: str,
        report: dict[str, Any],
        generated_at: datetime,
    ) -> dict[str, Any]:
        if not self.settings.echarts_vendor_path.exists():
            raise ReportRenderError(
                "缺少 vendor/echarts.min.js，请先执行 npm run sync:echarts"
            )
        echarts_source = self.settings.echarts_vendor_path.read_text(encoding="utf-8")
        template_name = (
            "custom_report.html.j2"
            if report["meta"]["report_type"] == "custom"
            else "report.html.j2"
        )
        template = self.environment.get_template(template_name)
        demo_mode = self.settings.effective_data_source_mode == "demo"
        html = template.render(report=report, echarts_source=echarts_source, demo_mode=demo_mode)
        if "报告结束" not in html:
            raise ReportRenderError("报告模板渲染结果不完整")

        folder = self.settings.reports_dir / f"{generated_at:%Y}" / f"{generated_at:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        internal_filename = f"{report_id}.html"
        final_path = folder / internal_filename
        temporary_path = folder / f".{internal_filename}.tmp"
        temporary_path.write_text(html, encoding="utf-8")
        os.replace(temporary_path, final_path)

        content = final_path.read_bytes()
        report_type = report["meta"]["report_type"]
        custom_period = bool(report["meta"].get("custom_period"))
        prefix = {
            "leadership": "领导报表" if custom_period else "领导月报",
            "workshop": "车间报表" if custom_period else "车间日报",
            "equipment": "设备报表",
            "custom": report["meta"]["title"],
        }[report_type]
        prefix = "".join(char for char in prefix if char not in '\\/:*?"<>|')[:50] or "自定义报表"
        if demo_mode:
            prefix = f"演示数据_{prefix}"
        period_start = report["meta"].get("period_start") or report["meta"]["report_date"]
        period_end = report["meta"].get("period_end") or period_start
        period = period_start if period_start == period_end else f"{period_start}_至_{period_end}"
        download_filename = f"{prefix}_{period}_{generated_at:%H%M%S}.html"
        return {
            "filename": download_filename,
            "relative_path": final_path.relative_to(self.settings.reports_dir).as_posix(),
            "file_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
