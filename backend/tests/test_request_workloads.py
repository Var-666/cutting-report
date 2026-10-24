from contextvars import ContextVar
from threading import BoundedSemaphore, Event
from types import SimpleNamespace

import anyio
import httpx
import pytest
from fastapi import HTTPException

from app.api import analysis, reports, workloads
from app.api.auth import require_admin, require_user
from app.config import Settings
from app.main import app
from app.services.request_cancellation import SqlRequestCancelled


def test_busy_analysis_pdf_and_queries_leave_default_threads_and_fast_routes_free(tmp_path, monkeypatch):
    release = Event()
    entered = {name: Event() for name in ("analysis", "pdf", "query")}
    for pool in (workloads.analysis_requests, workloads.pdf_requests, workloads.query_requests):
        monkeypatch.setattr(pool, "_slots", BoundedSemaphore(1))

    def block(name, result):
        entered[name].set()
        assert release.wait(5)
        return result

    pdf = tmp_path / "report.pdf"
    pdf.write_bytes(b"%PDF-1.7\nreport")
    monkeypatch.setattr(analysis, "_analyze_report", lambda *_: block("analysis", {"success": True}))
    monkeypatch.setattr(reports, "_resolve_report_file", lambda *_: ({"filename": "report.html"}, pdf))
    state = {
        "settings": Settings(project_root=tmp_path),
        "report_analysis_service": SimpleNamespace(settings=Settings(project_root=tmp_path)),
        "pdf_service": SimpleNamespace(render=lambda _: block("pdf", pdf)),
        "energy_period_service": SimpleNamespace(get_day=lambda *_, **__: block("query", {})),
        "worker": SimpleNamespace(status=lambda: {"report_alive": True}),
        "workshop": SimpleNamespace(
            authenticate=lambda *_: {"role": "admin", "user_id": "admin"},
            create_session=lambda *_, **__: ("session", "2026-10-24T23:00:00+08:00"),
        ),
        "devices": {},
    }
    for key, value in state.items():
        monkeypatch.setattr(app.state, key, value, raising=False)
    monkeypatch.setattr(analysis, "device_state", lambda _: app.state)
    # The real device dependency also runs before the isolated endpoint.
    state["devices"]["Cutter01"] = app.state
    monkeypatch.setitem(app.dependency_overrides, require_admin, lambda: {"role": "admin"})
    monkeypatch.setitem(app.dependency_overrides, require_user, lambda: {"role": "admin"})

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            requests = (
                ("POST", "/api/v1/analysis", {"report_type": "workshop", "report_date": "2026-10-24"}),
                ("GET", "/api/v1/reports/saved/download?format=pdf", None),
                ("GET", "/api/v1/energy-report/day?report_date=2026-10-24", None),
            )
            responses = []

            async def call(method, path, payload):
                responses.append(await client.request(method, path, json=payload))

            async with anyio.create_task_group() as tasks:
                try:
                    for args in requests:
                        tasks.start_soon(call, *args)
                    with anyio.fail_after(2):
                        while not all(event.is_set() for event in entered.values()):
                            await anyio.sleep(0.01)
                    assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
                    with anyio.fail_after(2):
                        for method, path, payload in requests:
                            busy = await client.request(method, path, json=payload)
                            assert busy.status_code == 503
                            assert busy.headers["retry-after"] == "1"
                        assert (await client.get("/api/v1/health")).status_code == 200
                        assert (await client.get("/api/v1/devices")).status_code == 200
                        assert (await client.get("/api/v1/reports/saved/download")).status_code == 200
                        login = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "password"})
                        assert login.status_code == 200
                        assert login.cookies["cutting_session"] == "session"
                finally:
                    release.set()
            assert len(responses) == 3
            assert all(response.status_code == 200 for response in responses)

    anyio.run(exercise)


@pytest.mark.parametrize("error", [ValueError("failed"), HTTPException(409, "conflict"), SqlRequestCancelled("cancelled")])
def test_isolation_preserves_context_and_exceptions_and_releases_slots(error):
    workload = workloads.RequestWorkload(1, "测试")
    context = ContextVar("workload-test", default=None)

    def fail():
        assert context.get() == "request-context"
        raise error

    async def exercise():
        token = context.set("request-context")
        try:
            with pytest.raises(type(error)) as caught:
                await workload.wrap(fail)()
            assert caught.value is error
            assert await workload.wrap(lambda: "recovered")() == "recovered"
            assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
        finally:
            context.reset(token)

    anyio.run(exercise)


def test_slow_hr_dependency_does_not_borrow_default_thread_tokens(monkeypatch):
    entered, release = Event(), Event()
    monkeypatch.setattr(workloads.authentication_requests, "_slots", BoundedSemaphore(1))
    user = {"user_id": "hr-user", "role": "admin", "auth_source": "hr_database", "external_id": "123"}

    def revalidate(*_, **__):
        entered.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr(app.state, "workshop", SimpleNamespace(user_for_session=lambda *_, **__: user), raising=False)
    monkeypatch.setattr(app.state, "worker_auth", SimpleNamespace(session_identity_available=revalidate), raising=False)
    monkeypatch.setattr(app.state, "worker", SimpleNamespace(status=lambda: {"report_alive": True}), raising=False)
    monkeypatch.setattr(app.state, "devices", {}, raising=False)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async def request():
                assert (await client.get("/api/v1/devices")).status_code == 200

            async with anyio.create_task_group() as tasks:
                try:
                    tasks.start_soon(request)
                    with anyio.fail_after(2):
                        while not entered.is_set():
                            await anyio.sleep(0.01)
                    assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
                    with anyio.fail_after(2):
                        assert (await client.get("/api/v1/health")).status_code == 200
                        assert (await client.get("/api/v1/devices")).status_code == 503
                finally:
                    release.set()

    anyio.run(exercise)


def test_cancelling_request_keeps_capacity_until_sync_work_finishes():
    workload = workloads.RequestWorkload(1, "测试")
    entered, release = Event(), Event()
    scopes = []

    def blocking():
        entered.set()
        assert release.wait(5)

    async def request():
        with anyio.CancelScope() as scope:
            scopes.append(scope)
            await workload.wrap(blocking)()

    async def exercise():
        async with anyio.create_task_group() as tasks:
            try:
                tasks.start_soon(request)
                with anyio.fail_after(2):
                    while not entered.is_set():
                        await anyio.sleep(0.01)
                scopes[0].cancel()
                await anyio.sleep(0)
                with pytest.raises(HTTPException) as caught:
                    await workload.wrap(lambda: None)()
                assert caught.value.status_code == 503
            finally:
                release.set()
        assert await workload.wrap(lambda: "recovered")() == "recovered"

    anyio.run(exercise)
