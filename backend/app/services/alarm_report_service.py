from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import Settings
from app.services.pdf_service import PdfService


class AlarmReportService:
    def __init__(self, settings: Settings, pdf_service: PdfService) -> None:
        self.settings = settings
        self.pdf_service = pdf_service
        template_dir = Path(__file__).resolve().parents[1] / "templates"
        self.environment = Environment(
            loader=FileSystemLoader(template_dir),
            autoescape=select_autoescape(["html", "xml", "j2"]),
        )

    def render(
        self,
        *,
        report_id: str,
        machine_no: str,
        analysis: dict[str, Any],
        generated_at: datetime,
    ) -> dict[str, Any]:
        template = self.environment.get_template("alarm_report.html.j2")
        html = template.render(
            report_id=report_id,
            machine_no=machine_no,
            analysis=analysis,
            generated_at=generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            severity_labels={"warning": "提示", "high": "高", "critical": "严重"},
        )
        folder = self.settings.reports_dir / "alarms" / f"{generated_at:%Y}" / f"{generated_at:%m}"
        folder.mkdir(parents=True, exist_ok=True)
        html_path = folder / f"{report_id}.html"
        temporary_path = folder / f".{report_id}.html.tmp"
        temporary_path.write_text(html, encoding="utf-8")
        os.replace(temporary_path, html_path)

        pdf_path = self.pdf_service.render(html_path)
        content = pdf_path.read_bytes()
        safe_machine = "".join(char for char in machine_no if char not in '\\/:*?"<>|')[:40]
        return {
            "html_relative_path": html_path.relative_to(self.settings.reports_dir).as_posix(),
            "pdf_relative_path": pdf_path.relative_to(self.settings.reports_dir).as_posix(),
            "pdf_filename": f"{safe_machine or '切割机'}_智能告警报告_{generated_at:%Y%m%d_%H%M%S}.pdf",
            "file_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
