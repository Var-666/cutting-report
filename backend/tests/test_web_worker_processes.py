from tests.line_fixtures import as_line_services, runtime_line
import multiprocessing as mp
from pathlib import Path
from queue import Empty
from time import monotonic, sleep
import threading

import anyio
import httpx
import pytest

from app.config import Settings
from tests.line_fixtures import configuration as line_configuration
from app.database.coordination import CoordinationRepository
from app.database.postgres import PostgresDatabase
from tests.postgres_fixtures import state_settings
from app.services.application_runtime import build_runtime


def configuration(root, postgres_state=None):
    from dataclasses import replace
    if isinstance(root, Settings):
        return root
    value = replace(line_configuration(Path(root)), app_env="test", alarm_monitor_enabled=False, worker_poll_seconds=0.05)
    return state_settings(value, postgres_state) if postgres_state else value


def _web_probe(root, output):
    import app.main as main
    main.settings = configuration(root)

    async def probe():
        before = {thread.ident for thread in threading.enumerate()}
        constructed = []
        original = threading.Thread.__init__

        def observe(self, *args, **kwargs):
            constructed.append(kwargs.get("name"))
            original(self, *args, **kwargs)

        threading.Thread.__init__ = observe
        async with main.lifespan(main.app):
            threading.Thread.__init__ = original
            assert all(name and name.startswith("pool-") for name in constructed)
            extra = [thread.name for thread in threading.enumerate() if thread.ident not in before]
            assert all(line.background.workers == () for line in main.app.state.runtime.database_runtime.snapshot().lines.values() if line.background is not None)
            job = main.app.state.runtime.metadata.get_job("restart-test")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                with anyio.fail_after(3):
                    assert (await client.get("/api/v1/health")).status_code == 200
                    response = await client.post("/api/v1/auth/login", json={"username": "process-admin", "password": "test-password"})
                    assert response.status_code == 200
                    assert (await client.get("/api/v1/devices")).status_code == 200
            output.put(([name for name in extra if not name.startswith("pool-")], job["status"], hasattr(main.app.state, "worker")))

    anyio.run(probe)


def _worker(root, stop, entered=None, release=None):
    from app.worker import WorkerProcess
    process = WorkerProcess(configuration(root))
    process.stop_event = stop
    if entered is not None:
        for device in (line.service for line in process.runtime.database_runtime.snapshot().lines.values() if line.pipeline is not None):
            original = device.pipeline.run

            def blocked(job, *, cancellation_check=None, pipeline_run=original, metadata=device.metadata):
                metadata.update_status(job["report_id"], "calculating", "blocked fixture")
                entered.set()
                assert release.wait(20)
                pipeline_run(job, cancellation_check=cancellation_check)

            device.pipeline.run = blocked
    process.run()


def _hold_model(root, output, release):
    from app.services.llm_service import llm_capacity
    from dataclasses import replace
    with PostgresDatabase(configuration(root)) as database:
        configured = replace(configuration(root), llm_max_concurrent=2, llm_queue_timeout_seconds=10)
        with llm_capacity(configured, database):
            output.put("entered")
            assert release.wait(15)


def _hold_pdf(root, output, release):
    from app.services.pdf_service import PdfRenderError, PdfService
    service = PdfService(max_waiters=0, coordination_path=Path(root) / "pdf-render.lock")
    try:
        with service._render_slot():
            output.put("entered")
            assert release.wait(15)
    except PdfRenderError:
        output.put("busy")


def _configuration_observer(root, output, changed):
    from app.services.automation_runtime import AutomationRuntime
    runtime = AutomationRuntime(configuration(root))
    old, revision = runtime.snapshot()
    output.put((old.smtp_host, revision))
    assert changed.wait(10)
    current, new_revision = runtime.snapshot()
    output.put((current.smtp_host, current.automation_enabled, new_revision > revision,
                current.database_sources_json == old.database_sources_json))


def _config_increment(path, start):
    from app.services.runtime_settings_store import read_runtime_env, write_runtime_env, runtime_settings_lock
    from app.core.process_lock import settings_file_lock
    assert start.wait(10)
    with runtime_settings_lock, settings_file_lock(Path(path)):
        current = read_runtime_env(Path(path))
        write_runtime_env(Path(path), {"TEST_COUNTER": int(current.get("TEST_COUNTER", 0)) + 1})


def _uncommitted(path, entered):
    with PostgresDatabase(path) as database, database.connect() as connection:
        connection.execute("INSERT INTO llm_waiters VALUES ('uncommitted',0)")
        entered.set()
        threading.Event().wait(20)


def _publish_cache(root):
    from app.services.cache_revision import CacheRevision
    from tests.line_fixtures import line_settings
    CacheRevision(line_settings(configuration(root)), "production").publish()
    from tests.line_fixtures import line_settings
    CacheRevision(line_settings(configuration(root)), "leadership").publish()


def _cleanup_after_push(root, download_id, entered, release, output):
    from test_file_downloads import make_service
    database = PostgresDatabase(root).__enter__()
    service, _ = make_service(root.project_root, database)
    original = service.repository.save

    def pause_before_commit(item):
        entered.set()
        assert release.wait(10)
        original(item)

    service.repository.save = pause_before_commit
    output.put(service.delete_after_push(download_id, dispatch_id="dispatch-process-test"))


def _analysis_child(root, output, background):
    from contextlib import nullcontext
    from dataclasses import replace
    from app.database.metadata import MetadataRepository
    from app.services.llm_service import background_llm_work, llm_capacity
    from app.services.report_analysis_service import ReportAnalysisService
    from tests.analysis_fakes import model_analysis
    with PostgresDatabase(configuration(root)) as database:
        configured = replace(configuration(root), llm_enabled=True, llm_max_concurrent=2, llm_queue_timeout_seconds=5)
        service = ReportAnalysisService(configured, MetadataRepository(database, device_id='Cutter01'))

        def model(report_type, context):
            output.put("model_started")
            with llm_capacity(configured, database):
                output.put("model_admitted")
                return model_analysis(report_type, context)

        service.llm.analyze_context = model
        with background_llm_work() if background else nullcontext():
            result = service._analyze(report_type="workshop", report_date="2026-10-24", context={"facts": [], "tables": []}, force=False)
            output.put(("result", result.cached))


def join(process):
    process.join(10)
    assert process.exitcode == 0


def test_web_restarts_do_not_reset_worker_and_crashed_worker_report_recovers(tmp_path, postgres_state):
    context = mp.get_context("spawn")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor/echarts.min.js").write_text("window.echarts={};")
    runtime = build_runtime(configuration(tmp_path, postgres_state), role="worker")
    runtime.workshop.bootstrap_admin(username="process-admin", display_name="进程测试", password="test-password",
                                    now="2026-10-24T10:00:00+08:00")
    runtime.database_runtime.snapshot().lines["Cutter01"].service.metadata.create_job(report_id="restart-test", report_type="equipment", report_date="2026-08-18",
        project_code=None, ship_no=None, request_id="test", created_at="2026-10-24T10:00:00+08:00")
    stop, entered, release = context.Event(), context.Event(), context.Event()
    worker = context.Process(target=_worker, args=(configuration(tmp_path, postgres_state), stop, entered, release))
    worker.start()
    try:
        assert entered.wait(10)
        for _ in range(2):
            output = context.Queue()
            web = context.Process(target=_web_probe, args=(configuration(tmp_path, postgres_state), output))
            web.start()
            assert output.get(timeout=10) == ([], "calculating", False)
            join(web)
        original_owner = runtime.metadata.get_job("restart-test")["execution_owner"]
        assert original_owner
    finally:
        worker.kill()
        worker.join(10)
    assert runtime.metadata.get_job("restart-test")["status"] == "calculating"
    # Simulate the elapsed lease timeout without a 30-second test sleep.
    with runtime.database.connect() as connection:
        connection.execute("UPDATE process_leases SET expires_at=0 WHERE name='worker:test-consumer'")
    replacement_stop = context.Event()
    replacement = context.Process(target=_worker, args=(configuration(tmp_path, postgres_state), replacement_stop))
    replacement.start()
    try:
        deadline = monotonic() + 10
        while monotonic() < deadline:
            job = runtime.metadata.get_job("restart-test")
            if job["status"] in {"completed", "failed"}:
                break
            sleep(0.05)
        assert job["status"] == "completed", job
        assert job["execution_owner"] != original_owner
        assert (tmp_path / "reports" / job["relative_path"]).is_file()
        assert runtime.coordination.worker_status('worker:test-consumer')["report_alive"]
    finally:
        replacement_stop.set()
        join(replacement)


def test_model_limit_is_shared_and_process_death_releases_permit(tmp_path, postgres_state):
    context = mp.get_context("spawn")
    output = context.Queue()
    releases = [context.Event() for _ in range(3)]
    processes = [context.Process(target=_hold_model, args=(configuration(tmp_path, postgres_state), output, releases[index])) for index in range(3)]
    try:
        for process in processes[:2]:
            process.start()
        assert output.get(timeout=10) == output.get(timeout=10) == "entered"
        processes[2].start()
        with pytest.raises(Empty):
            output.get(timeout=0.3)
        processes[0].kill()
        processes[0].join(10)
        assert output.get(timeout=10) == "entered"
    finally:
        for release, process in zip(releases[1:], processes[1:]):
            if process.pid:
                release.set()
                join(process)


def test_pdf_capacity_is_shared_and_crash_releases_os_locks(tmp_path):
    context = mp.get_context("spawn")
    output = context.Queue()
    first_release, rejected_release = context.Event(), context.Event()
    first = context.Process(target=_hold_pdf, args=(str(tmp_path), output, first_release))
    first.start()
    try:
        assert output.get(timeout=10) == "entered"
        rejected = context.Process(target=_hold_pdf, args=(str(tmp_path), output, rejected_release))
        rejected.start()
        assert output.get(timeout=10) == "busy"
        join(rejected)
    finally:
        first.kill()
        first.join(10)
    release = context.Event()
    replacement = context.Process(target=_hold_pdf, args=(str(tmp_path), output, release))
    replacement.start()
    try:
        assert output.get(timeout=10) == "entered"
    finally:
        release.set()
        join(replacement)


def test_settings_writers_and_hot_runtime_are_cross_process_safe(tmp_path):
    from app.services.runtime_settings_store import read_runtime_env, write_runtime_env
    context = mp.get_context("spawn")
    path = configuration(tmp_path).runtime_settings_env_path
    write_runtime_env(path, {"SMTP_HOST": "old-host"})
    output, changed = context.Queue(), context.Event()
    observer = context.Process(target=_configuration_observer, args=(str(tmp_path), output, changed))
    observer.start()
    assert output.get(timeout=10)[0] == "old-host"
    write_runtime_env(path, {"SMTP_HOST": "new-host", "AUTOMATION_ENABLED": True, "SQLSERVER_HOST": "new-db"})
    changed.set()
    assert output.get(timeout=10) == ("new-host", True, True, True)
    join(observer)
    start = context.Event()
    writers = [context.Process(target=_config_increment, args=(str(path), start)) for _ in range(3)]
    for writer in writers:
        writer.start()
    start.set()
    for writer in writers:
        join(writer)
    assert read_runtime_env(path)["TEST_COUNTER"] == "3"
    assert read_runtime_env(path)["SMTP_HOST"] == "new-host"


def test_postgres_rolls_back_process_killed_inside_write_transaction(tmp_path, postgres_state):
    context = mp.get_context("spawn")
    database = postgres_state[0]
    entered = context.Event()
    writer = context.Process(target=_uncommitted, args=(postgres_state[1], entered))
    writer.start()
    try:
        assert entered.wait(10)
    finally:
        writer.kill()
        writer.join(10)
    with database.connect() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM llm_waiters").fetchone()["n"] == 0
        connection.execute("INSERT INTO llm_waiters VALUES ('recovered',0)")


def test_foreground_process_promotes_and_reuses_another_process_generation(tmp_path, postgres_state):
    from dataclasses import replace
    from app.services.llm_service import background_llm_work, llm_capacity
    configured = replace(configuration(tmp_path, postgres_state), llm_max_concurrent=2, llm_queue_timeout_seconds=5)
    build_runtime(configured, role="worker")
    context, processes = mp.get_context("spawn"), []
    output = context.Queue()
    try:
        with background_llm_work(), llm_capacity(configured, postgres_state[0]):
            producer = context.Process(target=_analysis_child, args=(configuration(tmp_path, postgres_state), output, True))
            processes.append(producer)
            producer.start()
            assert output.get(timeout=10) == "model_started"
            with pytest.raises(Empty):
                output.get(timeout=0.15)
            page = context.Process(target=_analysis_child, args=(configuration(tmp_path, postgres_state), output, False))
            processes.append(page)
            page.start()
            outcomes = [output.get(timeout=10) for _ in range(3)]
            assert sorted(item[1] for item in outcomes if isinstance(item, tuple)) == [False, True]
            assert outcomes.count("model_admitted") == 1
            for process in processes:
                join(process)
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(10)


def test_worker_refresh_invalidates_only_its_device_local_caches(tmp_path, postgres_state, monkeypatch):
    from dataclasses import replace
    from datetime import date
    from app.services.query_service import QueryService, EquipmentQueryResult
    from app.models.domain import ReportFilter
    from app.services.leadership_report_service import LeadershipReportService
    configured = configuration(tmp_path, postgres_state)
    runtime = build_runtime(configured, role="worker")
    from tests.line_fixtures import line_settings
    first, second = QueryService(line_settings(configured)), QueryService(line_settings(configured, "Cutter02"))
    calls = []
    for label, service in (("one", first), ("two", second)):
        monkeypatch.setattr(service, "_fetch_equipment_uncached", lambda _, label=label: calls.append(label) or EquipmentQueryResult([]))
    filters = ReportFilter(report_date=date(2026, 10, 24))
    one, two = first.fetch_equipment_data(filters), second.fetch_equipment_data(filters)
    assert first.fetch_equipment_data(filters).records is one.records
    leader = LeadershipReportService(line_settings(configured), runtime.database_runtime.snapshot().lines["Cutter01"].service.leadership_report_service.snapshots)
    base = runtime.database_runtime.snapshot().lines["Cutter01"].service.leadership_report_service.fetch(date(2026, 10, 1))
    monkeypatch.setattr(leader, "_compose_snapshot_report", lambda *_, **__: replace(base))
    # The isolated demo result has a fresh snapshot, so the second call uses local cache.
    report = leader._fetch_snapshot_report(date(2026, 10, 1))
    assert leader._fetch_snapshot_report(date(2026, 10, 1)) is report
    context = mp.get_context("spawn")
    publisher = context.Process(target=_publish_cache, args=(configuration(tmp_path, postgres_state),))
    publisher.start()
    join(publisher)
    assert first.fetch_equipment_data(filters).records is not one.records
    assert second.fetch_equipment_data(filters).records is two.records
    assert calls == ["one", "two", "one"]
    assert leader._fetch_snapshot_report(date(2026, 10, 1)) is not report
    assert len(leader._cache) == 1, "revision changes must not accumulate old local cache keys"


def test_web_push_cleanup_and_worker_retention_preserve_one_cleanup_audit(tmp_path, postgres_state):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from test_file_downloads import make_service, ADMIN
    service, payload = make_service(tmp_path, postgres_state[0])
    downloaded = service.download(**payload, user=ADMIN)
    context = mp.get_context("spawn")
    entered, release, output = context.Event(), context.Event(), context.Queue()
    process = context.Process(target=_cleanup_after_push, args=(configuration(tmp_path, postgres_state), downloaded["download_id"], entered, release, output))
    process.start()
    try:
        assert entered.wait(10)
        with ThreadPoolExecutor(max_workers=1) as executor:
            cleanup = executor.submit(service.cleanup_expired, datetime.now(ZoneInfo(service.settings.timezone)) + timedelta(days=365))
            try:
                with pytest.raises(TimeoutError):
                    cleanup.result(timeout=0.15)
            finally:
                release.set()
            assert cleanup.result(timeout=5) == {"cleaned": 0, "missing": 0, "failed": 0}
        assert output.get(timeout=10)["deleted"]
        join(process)
        stored = service.repository.get(downloaded["download_id"])
        assert stored["cleanup_reason"] == "pushed"
        assert stored["cleanup_dispatch_id"] == "dispatch-process-test"
    finally:
        release.set()
        if process.is_alive():
            process.kill()
            process.join(10)
