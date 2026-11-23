import multiprocessing as mp
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from app.modules.reports.pdf import PdfRenderError, PdfService
from app.core.request_cancellation import SqlRequestCancelled, bind_request_id, reset_request_id, sql_request_cancellations


def orphaning_renderer(html_path, temporary_path):
    os.setsid()
    descendant_pid = Path(html_path).parent.parent / "report.pid"
    subprocess.Popen([
        sys.executable, "-c",
        "import os, signal, sys, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)",
        str(descendant_pid),
    ])
    deadline = monotonic() + 10
    while not descendant_pid.exists():
        if monotonic() > deadline:
            raise RuntimeError("descendant failed to start")
        sleep(0.01)
    Path(temporary_path).write_bytes(b"partial")
    if Path(html_path).read_text() == "exit":
        os._exit(7)
    sleep(60)


def successful_renderer(html_path, temporary_path):
    import pypdfium2 as pdfium

    if os.name == "posix":
        os.setsid()
    with pdfium.PdfDocument.new() as pdf:
        pdf.new_page(240, 240).close()
        pdf.save(temporary_path)


def process_running(pid):
    result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
    return result.returncode == 0 and not result.stdout.strip().startswith("Z")


def use_real_process(monkeypatch, target):
    context = mp.get_context("spawn")
    processes = []

    def create(**kwargs):
        process = context.Process(**{**kwargs, "target": target})
        processes.append(process)
        return process

    monkeypatch.setattr("app.modules.reports.pdf.mp.get_context", lambda _: SimpleNamespace(Process=create))
    return processes


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group regression; Windows uses taskkill")
@pytest.mark.parametrize("root_state", ["exit", "timeout"])
def test_cleanup_kills_descendant_even_after_renderer_root_exits(tmp_path, monkeypatch, root_state):
    processes = use_real_process(monkeypatch, orphaning_renderer)
    html = tmp_path / "report.html"
    html.write_text(root_state)
    service = PdfService(max_waiters=0, operation_timeout_seconds=2)
    pid_path = html.with_suffix(".pid")
    try:
        with pytest.raises(PdfRenderError, match="异常退出" if root_state == "exit" else "超时"):
            service.render(html)
        assert pid_path.exists(), "fixture must create a live descendant before exercising cleanup"
        pid = int(pid_path.read_text())
        deadline = monotonic() + 2
        while process_running(pid) and monotonic() < deadline:
            sleep(0.02)
        assert not process_running(pid), "renderer cleanup must not leave a SIGTERM-resistant descendant running"
        assert not html.with_suffix(".pdf").exists()
        assert not list(tmp_path.glob(".report-*"))
        with pytest.raises(ValueError, match="closed"):
            processes[0].is_alive()
        with service._render_slot():
            pass
    finally:
        if pid_path.exists():
            pid = int(pid_path.read_text())
            if process_running(pid):
                os.kill(pid, signal.SIGKILL)
        for process in processes:
            try:
                if process.is_alive():
                    process.kill()
                process.join(2)
                process.close()
            except ValueError:
                pass


def test_completed_renders_close_native_process_handles_and_admit_next_task(tmp_path, monkeypatch):
    processes = use_real_process(monkeypatch, successful_renderer)
    service = PdfService(max_waiters=0, operation_timeout_seconds=5)
    try:
        for index in range(3):
            html = tmp_path / f"report-{index}.html"
            html.write_text("ready")
            assert service.render(html).read_bytes().startswith(b"%PDF-")
            with pytest.raises(ValueError, match="closed"):
                processes[-1].is_alive()
    finally:
        for process in processes:
            try:
                if process.is_alive():
                    process.kill()
                process.join(2)
                process.close()
            except ValueError:
                pass


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group regression; Windows uses taskkill")
def test_cancelling_active_download_kills_chromium_and_releases_slot(tmp_path, monkeypatch):
    use_real_process(monkeypatch, orphaning_renderer)
    html = tmp_path / "report.html"
    html.write_text("stall")
    service = PdfService(max_waiters=0, operation_timeout_seconds=10)
    execution = sql_request_cancellations.activate("pdf-active-cancel")
    def render():
        token = bind_request_id(execution)
        try:
            service.render(html)
        finally:
            reset_request_id(token)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            output = pool.submit(render)
            marker = html.with_suffix(".pid")
            deadline = monotonic() + 5
            while not marker.exists() and monotonic() < deadline:
                sleep(0.02)
            assert marker.exists(), "Chromium fixture must be active before cancellation"
            sql_request_cancellations.cancel(execution)
            with pytest.raises(SqlRequestCancelled):
                output.result(timeout=5)
        pid = int(marker.read_text())
        deadline = monotonic() + 2
        while process_running(pid) and monotonic() < deadline:
            sleep(0.02)
        assert not process_running(pid)
        assert not list(tmp_path.glob(".report-*")) and not html.with_suffix(".pdf").exists()
        with service._render_slot():
            pass
    finally:
        sql_request_cancellations.finish(execution)
