from __future__ import annotations

import hashlib
import os
import logging
import re
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
        execution_id: str | None = None,
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
        if execution_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", execution_id):
            raise ValueError("无效的告警报告执行标识")
        stem = f"{report_id}_{execution_id}" if execution_id else report_id
        html_path = folder / f"{stem}.html"
        temporary_path = folder / f".{stem}.html.tmp"
        try:
            temporary_path.write_text(html, encoding="utf-8")
            os.replace(temporary_path, html_path)
            pdf_path = self.pdf_service.render(html_path)
            content = pdf_path.read_bytes()
        except Exception:
            self._remove_paths([temporary_path, html_path, html_path.with_suffix(".pdf"),
                                folder / f".{stem}.pdf.tmp"])
            raise
        safe_machine = "".join(char for char in machine_no if char not in '\\/:*?"<>|')[:40]
        return {
            "html_relative_path": html_path.relative_to(self.settings.reports_dir).as_posix(),
            "pdf_relative_path": pdf_path.relative_to(self.settings.reports_dir).as_posix(),
            "pdf_filename": f"{safe_machine or '切割机'}_智能告警报告_{generated_at:%Y%m%d_%H%M%S}.pdf",
            "file_size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }

    @staticmethod
    def _remove_paths(paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("alarm attempt artifact cleanup failed", exc_info=True)

    def discard(self, artifact: dict[str, Any]) -> None:
        root = self.settings.reports_dir.resolve()
        paths = []
        for key in ("html_relative_path", "pdf_relative_path"):
            path = (root / artifact[key]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("告警报告清理路径越界")
            paths.append(path)
        self._remove_paths(paths)
