from __future__ import annotations

import base64
import json
import logging
import multiprocessing as mp
import os
import shutil
import signal
import subprocess
import threading
from tempfile import TemporaryDirectory
from contextlib import contextmanager, suppress
from hashlib import file_digest
from time import monotonic

from app.core.request_cancellation import (
    SqlRequestCancelled, current_request_id, sql_request_cancellations,
)
from pathlib import Path
from app.core.process_lock import FileLock
from app.core.operations_logging import event
from app.modules.reports.generation.email import EmailReport, render_email_report


class PdfRenderError(RuntimeError):
    pass


def _file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def _pdf_stamp(path: Path, source_hash: str) -> str:
    info = path.stat()
    return f"{source_hash}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}"


def _check_pdf(path: Path) -> None:
    import pypdfium2 as pdfium

    try:
        with path.open("rb") as stream:
            if not stream.read(8).startswith(b"%PDF-"):
                raise ValueError("missing PDF header")
            stream.seek(max(0, path.stat().st_size - 1024))
            if not stream.read().rstrip().endswith(b"%%EOF"):
                raise ValueError("missing PDF trailer")
        with pdfium.PdfDocument(path) as document:
            if len(document) == 0:
                raise ValueError("empty document")
    except (OSError, ValueError, pdfium.PdfiumError) as exc:
        raise PdfRenderError("PDF 渲染未生成完整文件，请稍后重试。") from exc


def _render_pdf_child(html_path: str, temporary_path: str, email: bool = False) -> None:
    """Run Chromium outside the server process so the parent can terminate it."""
    if os.name == "posix":
        try:
            os.setsid()
        except OSError:
            logging.getLogger('app.services.pdf_service').debug("could not create PDF renderer process group", exc_info=True)
    output = Path(temporary_path)
    parent = mp.parent_process()
    rendered = threading.Event()
    if parent is not None:
        def watch_parent() -> None:
            while not rendered.wait(0.2):
                if not parent.is_alive():
                    shutil.rmtree(output.parent, ignore_errors=True)
                    if os.name == "posix" and os.getpgrp() == os.getpid():
                        os.killpg(os.getpgrp(), signal.SIGKILL)
                    os._exit(1)

        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        if email:
            PdfService._render_email_with_pdf(Path(html_path), output)
        else:
            PdfService._render_with_playwright(Path(html_path), output)
    finally:
        rendered.set()


class PdfService:
    """Render a self-contained HTML report with Chromium's print engine."""

    def __init__(
        self,
        *,
        max_waiters: int = 4,
        queue_timeout_seconds: float = 30,
        operation_timeout_seconds: float | None = None,
        coordination_path: Path | None = None,
    ) -> None:
        if isinstance(max_waiters, bool) or not 0 <= max_waiters <= 100:
            raise ValueError("PDF 最大等待数量必须在 0 到 100 之间")
        if not 0 < queue_timeout_seconds <= 300:
            raise ValueError("PDF 排队期限必须在 0 到 300 秒之间")
        if operation_timeout_seconds is not None and not 1 <= operation_timeout_seconds <= 1800:
            raise ValueError("PDF 渲染期限必须在 1 到 1800 秒之间")
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(max_waiters + 1)
        self._admission_count = max_waiters + 1
        self.queue_timeout_seconds = queue_timeout_seconds
        self.operation_timeout_seconds = operation_timeout_seconds
        self.coordination_path = coordination_path

    @staticmethod
    def _check_cancelled() -> None:
        if sql_request_cancellations.is_cancelled(current_request_id.get()):
            raise SqlRequestCancelled("PDF 请求已取消")

    @contextmanager
    def _render_slot(self):
        self._check_cancelled()
        if not self._slots.acquire(blocking=False):
            raise PdfRenderError("PDF 生成队列已满，请稍后重试。")
        process_admission = None
        if self.coordination_path:
            try:
                for index in range(self._admission_count):
                    candidate = FileLock(self.coordination_path.with_name(f"pdf-admission-{index}.lock"))
                    if candidate.acquire(blocking=False):
                        process_admission = candidate
                        break
                if process_admission is None:
                    raise PdfRenderError("PDF 生成队列已满，请稍后重试。")
            except BaseException:
                self._slots.release()
                raise
        acquired = False
        process_lock = FileLock(self.coordination_path) if self.coordination_path else None
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
                    if process_lock is not None:
                        while not process_lock.acquire(blocking=False):
                            self._check_cancelled()
                            if monotonic() >= deadline:
                                raise PdfRenderError("PDF 生成排队超时，请稍后重试。")
                            threading.Event().wait(0.1)
                    yield
                    return
        finally:
            if process_lock:
                process_lock.release()
            if process_admission:
                process_admission.release()
            if acquired:
                self._lock.release()
            self._slots.release()

    def render(self, html_path: Path) -> Path:
        self._check_cancelled()
        pdf_path = html_path.with_suffix(".pdf")
        stamp = pdf_path.with_suffix(".pdf.source")

        def cache_hit(source_hash: str) -> bool:
            try:
                return stamp.read_text(encoding="ascii") == _pdf_stamp(pdf_path, source_hash)
            except (OSError, UnicodeError):
                return False

        try:
            source_hash = _file_hash(html_path)
            if cache_hit(source_hash):
                return pdf_path
            with self._render_slot():
                source_hash = _file_hash(html_path)
                if cache_hit(source_hash):
                    return pdf_path
                with TemporaryDirectory(prefix=f".{html_path.stem}-", dir=html_path.parent) as directory:
                    work = Path(directory)
                    temporary_path = work / "report.pdf"
                    snapshot = work / "report.html"
                    snapshot.write_bytes(html_path.read_bytes())
                    if _file_hash(snapshot) != source_hash:
                        raise PdfRenderError("归档 HTML 已更新，请重新下载 PDF。")
                    if self.operation_timeout_seconds is None:
                        self._render_with_playwright(snapshot, temporary_path)
                    else:
                        self._render_in_process(snapshot, temporary_path)
                    self._check_cancelled()
                    _check_pdf(temporary_path)
                    if _file_hash(html_path) != source_hash:
                        raise PdfRenderError("归档 HTML 已更新，请重新下载 PDF。")
                    marker = work / "source"
                    marker.write_text(_pdf_stamp(temporary_path, source_hash), encoding="ascii")
                    os.replace(temporary_path, pdf_path)
                    os.replace(marker, stamp)
        except SqlRequestCancelled:
            raise
        except Exception as exc:
            event(logging.getLogger('app.services.pdf_service'), logging.ERROR, 'PDF 渲染失败', error_code='PDF_RENDER_FAILED',
                  related_kind='reports', stage='rendering', incident=True, exc_info=True)
            if isinstance(exc, PdfRenderError):
                raise
            raise PdfRenderError("PDF 生成失败，请确认归档 HTML 与服务器 Chromium 可用。") from exc
        return pdf_path

    def render_email(self, html_path: Path, *, source_hash: str | None = None) -> EmailReport:
        with self._render_slot(), TemporaryDirectory(prefix="report-email-") as directory:
            output = Path(directory) / "email.json"
            try:
                snapshot = Path(directory) / "report.html"
                snapshot.write_bytes(html_path.read_bytes())
                if source_hash and _file_hash(snapshot) != source_hash:
                    raise PdfRenderError("归档 HTML 已更新，暂不能生成匹配的邮件正文。")
                if self.operation_timeout_seconds is None:
                    self._render_email_with_pdf(snapshot, output)
                else:
                    self._render_in_process(snapshot, output, email=True)
                self._check_cancelled()
                result = json.loads(output.read_text(encoding="utf-8"))
                return EmailReport(result["html"], {
                    cid: base64.b64decode(data) for cid, data in result["images"].items()
                })
            except Exception as exc:
                if isinstance(exc, SqlRequestCancelled):
                    raise
                if isinstance(exc, PdfRenderError):
                    raise PdfRenderError(f"邮件正文生成失败：{exc}") from exc
                raise PdfRenderError("邮件正文生成失败，请确认 PDF 渲染和转图片依赖已安装。") from exc

    @staticmethod
    def _render_email_with_pdf(html_path: Path, output: Path) -> None:
        pdf_path = output.with_suffix(".pdf")
        PdfService._render_with_playwright(html_path, pdf_path)
        _check_pdf(pdf_path)
        render_email_report(pdf_path, output)

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
                script_errors = []
                page.on("pageerror", lambda _error: script_errors.append(True))
                # Reports contain their charts and assets; no network access is needed.
                page.route("**/*", lambda route: route.abort())
                page.emulate_media(media="print")
                html = html_path.read_text(encoding="utf-8")
                page.set_content(html, wait_until="load", timeout=30_000)
                page.add_style_tag(content="""
                    @media print {
                        @page { size:auto; margin:0; }
                        @page leadership { size:auto; margin:0; }
                        html,body { margin:0; padding:0; page:auto!important; }
                        /* A continuous PDF uses its content height, without chapter page breaks. */
                        * { break-before:auto!important; break-after:auto!important; }
                        .screen-only { display:table!important; }
                        .print-only { display:none!important; }
                        tfoot { display:table-row-group!important; }
                    }
                """)
                if "window.__REPORT_READY__" in html:
                    page.wait_for_function("window.__REPORT_READY__ === true", timeout=30_000)
                ready = page.evaluate("""async () => {
                    await document.fonts.ready;
                    document.documentElement.classList.add('report-printing');
                    if (window.prepareIndustrialReport) await window.prepareIndustrialReport();
                    document.querySelectorAll('.chart').forEach(element => {
                        const chart = window.echarts?.getInstanceByDom(element);
                        if (chart) {
                            chart.setOption({animation: false});
                            chart.resize({animation: {duration: 0}});
                        }
                    });
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    return document.body.innerText.trim() !== '' && [...document.querySelectorAll('.chart[id]')]
                        .filter(element => element.getClientRects().length && getComputedStyle(element).visibility === 'visible')
                        .every(element => Boolean(window.echarts?.getInstanceByDom(element)));
                }""")
                if script_errors or not ready:
                    raise PdfRenderError("报表内容或图表未完整加载，请重试或下载已归档 HTML。")
                size = page.evaluate("""() => {
                    const root = document.querySelector('.leadership-print') || document.querySelector('.industrial-document') || document.querySelector('.page') || document.body;
                    const rect = root.getBoundingClientRect();
                    return {width: Math.ceil(Math.max(rect.width, root.scrollWidth) + Math.max(0, rect.left) * 2),
                            height: Math.ceil(Math.max(rect.height, root.scrollHeight) + Math.max(0, rect.top) + 4)};
                }""")
                page.pdf(
                    path=str(temporary_path),
                    width=f"{max(240, size['width'])}px",
                    height=f"{max(240, size['height'])}px",
                    print_background=True,
                    prefer_css_page_size=False,
                    display_header_footer=False,
                )
            finally:
                browser.close()

    def _render_in_process(self, html_path: Path, temporary_path: Path, *, email: bool = False) -> None:
        process = None
        try:
            process = mp.get_context("spawn").Process(
                target=_render_pdf_child,
                args=(str(html_path), str(temporary_path), True) if email else (str(html_path), str(temporary_path)),
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
            with suppress(OSError):
                os.killpg(pid, signal.SIGTERM)
        elif pid and os.name == "nt" and process.is_alive():
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        # The renderer root may have exited while Chromium descendants still
        # run, including processes that ignored SIGTERM. Cleanup the owned
        # session regardless of the root's current liveness.
        if pid and os.name == "posix":
            with suppress(OSError):
                os.killpg(pid, signal.SIGKILL)
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
