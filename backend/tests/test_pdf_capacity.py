from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.pdf_service import PdfService, PdfRenderError
from app.services.request_cancellation import (
    SqlRequestCancelled, bind_request_id, reset_request_id, sql_request_cancellations,
)


def test_full_pdf_queue_rejects_extra_waiter_and_releases_after_timeout():
    service = PdfService(max_waiters=0, queue_timeout_seconds=0.02)
    with service._render_slot():
        with pytest.raises(PdfRenderError, match="队列已满"):
            with service._render_slot():
                pytest.fail("a full queue must never admit extra rendering work")
    # A rejected admission cannot consume capacity permanently.
    with service._render_slot():
        pass
    service._lock.acquire()
    try:
        with pytest.raises(PdfRenderError, match="排队超时"):
            with service._render_slot():
                pytest.fail("timed out waiter cannot start rendering")
    finally:
        service._lock.release()
    with service._render_slot():
        pass


def test_cancel_while_waiting_returns_without_starting_browser_and_restores_slot():
    service = PdfService(max_waiters=1, queue_timeout_seconds=3)
    execution = sql_request_cancellations.activate("pdf-wait")
    entered, finished = Event(), Event()
    errors = []
    service._lock.acquire()

    def wait():
        token = bind_request_id(execution)
        try:
            entered.set()
            with service._render_slot():
                errors.append("unexpected render")
        except Exception as exc:
            errors.append(exc)
        finally:
            reset_request_id(token)
            finished.set()

    thread = Thread(target=wait)
    thread.start()
    try:
        assert entered.wait(1)
        sql_request_cancellations.cancel(execution)
        assert finished.wait(1), "cancelled queued PDF must not wait for the active render"
        assert len(errors) == 1 and isinstance(errors[0], SqlRequestCancelled)
    finally:
        service._lock.release()
        thread.join(1)
        sql_request_cancellations.finish(execution)
    with service._render_slot():
        pass


def test_render_exception_always_releases_capacity():
    service = PdfService(max_waiters=0)
    with pytest.raises(RuntimeError):
        with service._render_slot():
            raise RuntimeError("render failed")
    with service._render_slot():
        pass


def test_isolated_renderer_is_terminated_after_operation_deadline(tmp_path, monkeypatch):
    class StuckProcess:
        exitcode = None

        def __init__(self, **_kwargs):
            self.terminated = False
            self.closed = False

        def start(self):
            pass

        def is_alive(self):
            return not self.terminated

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

        def close(self):
            self.closed = True

    process = StuckProcess()
    monkeypatch.setattr(
        "app.services.pdf_service.mp.get_context",
        lambda _name: SimpleNamespace(Process=lambda **kwargs: process),
    )
    service = PdfService(operation_timeout_seconds=1)
    service.operation_timeout_seconds = 0.01

    with pytest.raises(PdfRenderError, match="超时"):
        service._render_in_process(Path(tmp_path / "report.html"), Path(tmp_path / ".report.pdf.tmp"))
    assert process.terminated is True
    assert process.closed is True


def test_settings_reject_pdf_render_timeout_outside_process_bound(tmp_path):
    with pytest.raises(ValueError, match="PDF_RENDER_TIMEOUT_SECONDS"):
        Settings(project_root=tmp_path, pdf_render_timeout_seconds=0)


@pytest.mark.parametrize("kwargs", [
    {"max_waiters": -1}, {"max_waiters": 101}, {"max_waiters": True},
    {"queue_timeout_seconds": 0}, {"queue_timeout_seconds": float("nan")},
    {"queue_timeout_seconds": float("inf")},
    {"operation_timeout_seconds": 0}, {"operation_timeout_seconds": 1801},
])
def test_pdf_capacity_rejects_invalid_limits(kwargs):
    with pytest.raises(ValueError):
        PdfService(**kwargs)


def test_unexpected_html_decode_error_cleans_temp_and_closes_browser(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from contextlib import contextmanager

    html = tmp_path / 'broken.html'
    html.write_bytes(b'\xff')
    temporary = tmp_path / '.broken.pdf.tmp'
    temporary.write_bytes(b'partial')
    closed = []
    page = SimpleNamespace(route=lambda *args: None, emulate_media=lambda **kwargs: None)
    browser = SimpleNamespace(new_page=lambda **kwargs: page, close=lambda: closed.append(True))

    @contextmanager
    def playwright():
        yield SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))

    monkeypatch.setattr('playwright.sync_api.sync_playwright', playwright)
    renderer = PdfService()
    with pytest.raises(PdfRenderError):
        renderer.render(html)
    assert closed == [True]
    assert not temporary.exists()
    assert not html.with_suffix('.pdf').exists()
    with renderer._render_slot():
        pass
