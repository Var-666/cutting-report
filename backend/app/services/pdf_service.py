from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from time import monotonic

from app.services.request_cancellation import (
    SqlRequestCancelled, current_request_id, sql_request_cancellations,
)
from pathlib import Path


class PdfRenderError(RuntimeError):
    pass


def _render_pdf_child(html_path: str, temporary_path: str) -> None:
    """Run Chromium outside the server process so the parent can terminate it."""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            logging.getLogger(__name__).debug("could not create PDF renderer process group", exc_info=True)
    PdfService._render_with_playwright(Path(html_path), Path(temporary_path))


class PdfService:
    """Render a self-contained HTML report with Chromium's print engine."""

    def __init__(
        self,
        *,
        max_waiters: int = 4,
        queue_timeout_seconds: float = 30,
        operation_timeout_seconds: float | None = None,
    ) -> None:
        if isinstance(max_waiters, bool) or not isinstance(max_waiters, int) or not 0 <= max_waiters <= 100:
            raise ValueError("PDF 最大等待数量必须在 0 到 100 之间")
        if not 0 < queue_timeout_seconds <= 300:
            raise ValueError("PDF 排队期限必须在 0 到 300 秒之间")
        if operation_timeout_seconds is not None and not 1 <= operation_timeout_seconds <= 1800:
            raise ValueError("PDF 渲染期限必须在 1 到 1800 秒之间")
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_waiters + 1)
        self.queue_timeout_seconds = queue_timeout_seconds
        self.operation_timeout_seconds = operation_timeout_seconds

    @staticmethod
    def _check_cancelled() -> None:
        if sql_request_cancellations.is_cancelled(current_request_id.get()):
            raise SqlRequestCancelled("PDF 请求已取消")

    @contextmanager
    def _render_slot(self):
        self._check_cancelled()
        if not self._slots.acquire(blocking=False):
            raise PdfRenderError("PDF 生成队列已满，请稍后重试。")
        acquired = False
        deadline = monotonic() + self.queue_timeout_seconds
        try:
            while True:
                self._check_cancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise PdfRenderError("PDF 生成排队超时，请稍后重试。")
                if self._lock.acquire(timeout=min(0.1, remaining)):
                    acquired = True
                    self._check_cancelled()
                    yield
                    return
        finally:
            if acquired:
                self._lock.release()
            self._slots.release()

    def render(self, html_path: Path) -> Path:
        # The isolated renderer preserves display_header_footer=True with the
        # <span class="pageNumber"></span> / <span class="totalPages"></span>
        # footer for every production PDF.
        self._check_cancelled()
        pdf_path = html_path.with_suffix(".pdf")
        if pdf_path.is_file() and pdf_path.stat().st_mtime_ns >= html_path.stat().st_mtime_ns:
            return pdf_path

        with self._render_slot():
            if pdf_path.is_file() and pdf_path.stat().st_mtime_ns >= html_path.stat().st_mtime_ns:
                return pdf_path
            temporary_path = pdf_path.with_name(f".{pdf_path.name}.tmp")
            try:
                if self.operation_timeout_seconds is None:
                    self._render_with_playwright(html_path, temporary_path)
                else:
                    self._render_in_process(html_path, temporary_path)
                self._check_cancelled()
                os.replace(temporary_path, pdf_path)
            except Exception as exc:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).exception("PDF temporary file cleanup failed")
                if isinstance(exc, (SqlRequestCancelled, PdfRenderError)):
                    raise
                logging.getLogger(__name__).exception("PDF rendering failed")
                raise PdfRenderError(
                    "PDF 生成失败，请确认服务器已安装 Playwright Chromium。"
                ) from exc
        return pdf_path

    @staticmethod
    def _render_with_playwright(html_path: Path, temporary_path: Path) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise PdfRenderError("服务器未安装 PDF 渲染依赖，请联系管理员完成安装。") from exc
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1380, "height": 900})
                # Reports contain their charts and assets; no network access is needed.
                page.route("**/*", lambda route: route.abort())
                page.emulate_media(media="print")
                html = html_path.read_text(encoding="utf-8")
                page.set_content(html, wait_until="load", timeout=30_000)
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
                    display_header_footer=True,
                    header_template="<div></div>",
                    footer_template="""
                        <div style="width:100%;display:flex;justify-content:space-between;
                            padding:0 10mm;color:#6b8291;font-size:8px;
                            font-family:'Microsoft YaHei','PingFang SC',sans-serif;">
                          <span>切割生产智能产报系统</span>
                          <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
                        </div>
                    """,
                )
            finally:
                browser.close()

    def _render_in_process(self, html_path: Path, temporary_path: Path) -> None:
        process = None
        try:
            process = mp.get_context("spawn").Process(
                target=_render_pdf_child,
                args=(str(html_path), str(temporary_path)),
                name="pdf-renderer",
            )
            process.start()
            deadline = monotonic() + self.operation_timeout_seconds
            while process.is_alive():
                self._check_cancelled()
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise PdfRenderError("PDF 渲染超时，Chromium 进程已终止，请稍后重试。")
                process.join(timeout=min(0.1, remaining))
            if process.exitcode != 0:
                raise PdfRenderError("PDF 渲染进程异常退出，请稍后重试。")
            if not temporary_path.is_file():
                raise PdfRenderError("PDF 渲染进程未生成有效文件，请稍后重试。")
        finally:
            if process is not None:
                try:
                    self._terminate_process_tree(process)
                finally:
                    if not process.is_alive():
                        process.close()

    @staticmethod
    def _terminate_process_tree(process) -> None:
        if process is None:
            return
        pid = getattr(process, "pid", None)
        if pid and os.name == "posix":
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        elif pid and os.name == "nt" and process.is_alive():
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        # The renderer root may have exited while Chromium descendants still
        # run, including processes that ignored SIGTERM. Cleanup the owned
        # session regardless of the root's current liveness.
        if pid and os.name == "posix":
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
