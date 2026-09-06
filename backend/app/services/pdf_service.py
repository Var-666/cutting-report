from __future__ import annotations

import logging
import os
import threading
from pathlib import Path


class PdfRenderError(RuntimeError):
    pass


class PdfService:
    """Render a self-contained HTML report with Chromium's print engine."""

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def render(self, html_path: Path) -> Path:
        pdf_path = html_path.with_suffix(".pdf")
        if pdf_path.is_file() and pdf_path.stat().st_mtime_ns >= html_path.stat().st_mtime_ns:
            return pdf_path

        with self._lock:
            if pdf_path.is_file() and pdf_path.stat().st_mtime_ns >= html_path.stat().st_mtime_ns:
                return pdf_path
            temporary_path = pdf_path.with_name(f".{pdf_path.name}.tmp")
            try:
                from playwright.sync_api import Error as PlaywrightError
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise PdfRenderError("服务器未安装 PDF 渲染依赖，请联系管理员完成安装。") from exc
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        page = browser.new_page(viewport={"width": 1380, "height": 900})
                        # Reports contain their charts and assets; no network access is needed.
                        page.route("**/*", lambda route: route.abort())
                        page.emulate_media(media="print")
                        html = html_path.read_text(encoding="utf-8")
                        page.set_content(
                            html,
                            wait_until="load",
                            timeout=30_000,
                        )
                        # Let the print engine paginate full tables, preserving their final
                        # totals. Legacy fixed-row print copies can omit totals or overflow.
                        page.add_style_tag(content="""
                            @media print {
                                .screen-only { display:table!important; }
                                .print-only { display:none!important; }
                                tfoot { display:table-row-group!important; }
                                .analysis-fact { break-inside:avoid; padding:6px 10px; }
                                .analysis-fact .label { display:inline; }
                                .analysis-facts { gap:6px; }
                                .analysis-grid { gap:8px; }
                                .analysis-card { padding:10px 12px; }
                            }
                        """)
                        if "window.__REPORT_READY__" in html:
                            page.wait_for_function("window.__REPORT_READY__ === true", timeout=30_000)
                        page.evaluate("""async () => {
                            await document.fonts.ready;
                            document.documentElement.classList.add('report-printing');
                            document.querySelectorAll('.chart').forEach(element => {
                                const chart = window.echarts?.getInstanceByDom(element);
                                if (chart) {
                                    chart.setOption({animation: false});
                                    chart.resize({animation: {duration: 0}});
                                }
                            });
                            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                        }""")
                        page.pdf(
                            path=str(temporary_path),
                            landscape=True,
                            print_background=True,
                            prefer_css_page_size=True,
                            format="A4",
                        )
                    finally:
                        browser.close()
                os.replace(temporary_path, pdf_path)
            except (PlaywrightError, OSError) as exc:
                temporary_path.unlink(missing_ok=True)
                logging.getLogger(__name__).exception("PDF rendering failed")
                raise PdfRenderError(
                    "PDF 生成失败，请确认服务器已安装 Playwright Chromium。"
                ) from exc
        return pdf_path
