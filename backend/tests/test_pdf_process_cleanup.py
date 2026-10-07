import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from app.services.pdf_service import PdfRenderError, PdfService


def orphaning_renderer(html_path, temporary_path):
    os.setsid()
    descendant_pid = Path(html_path).with_suffix(".pid")
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
    if os.name == "posix":
        os.setsid()
    Path(temporary_path).write_bytes(b"%PDF-test")


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

    monkeypatch.setattr("app.services.pdf_service.mp.get_context", lambda _: SimpleNamespace(Process=create))
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
        assert not (tmp_path / ".report.pdf.tmp").exists()
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
            assert service.render(html).read_bytes() == b"%PDF-test"
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
