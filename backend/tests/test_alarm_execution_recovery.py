import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from app.config import settings
from app.database.alarms import AlarmLeaseLostError, AlarmRepository
from app.services.alarm_report_service import AlarmReportService
from test_alarm_recheck import NOW, START, monitor
from test_alarm_monitor import alarm


class Source:
    def latest_id(self):
        return 1

    def fetch_new(self, after, through, limit):
        return [alarm(1)] if after < 1 else []

    def fetch_recent(self, *args):
        return []

    def fetch_by_ids(self, ids):
        return [alarm(1)] if 1 in ids else []


@pytest.fixture
def repository(tmp_path):
    repository = AlarmRepository(tmp_path / "metadata.db")
    repository.initialize()
    repository.configure_monitoring_window(START)
    repository.ingest([alarm(1)], now=NOW, cursor=1, debounce_minutes=10)
    return repository


def reserve(repository, owner=None, now=NOW):
    incidents = repository.list_unreported_incidents()
    ids = sorted(item["incident_id"] for item in incidents)
    key = hashlib.sha256("\n".join(ids).encode()).hexdigest()
    return repository.reserve_report(proposed_report_id="interrupted", emission_key=key,
        machine_no="20kw切割机", incident_ids=ids, now=now.isoformat(),
        lease_owner=owner, lease_now=now)


def test_restart_recovers_report_associations_and_completes_once(repository):
    # Simulate an old process dying after durable reservation, before AI/PDF.
    assert repository.try_acquire_lease("dead-process", NOW, ttl_seconds=1)
    reserve(repository, "dead-process")
    assert repository.list_unreported_incidents() == []
    restarted = AlarmRepository(repository.database_path)
    restarted.initialize()
    service = monitor(restarted, Source())
    result = service.run_once(NOW + timedelta(seconds=2))
    assert result["report_id"] == "interrupted"
    assert result["report_status"] == "completed"
    assert restarted.list_unreported_incidents() == []
    assert service.run_once(NOW + timedelta(seconds=3))["report_status"] == "idle"
    assert len(restarted.list_reports()) == 1


def test_expired_owner_cannot_renew_ingest_complete_fail_or_clear_new_lease(repository):
    assert repository.try_acquire_lease("old", NOW, ttl_seconds=1)
    report_id = reserve(repository, "old")
    later = NOW + timedelta(seconds=2)
    assert not repository.renew_lease("old", later)
    assert repository.try_acquire_lease("new", later)
    assert repository.recover_interrupted_reports("new", later) == 1
    assert reserve(repository, "new", later) == report_id
    artifact = dict(html_relative_path="new.html", pdf_relative_path="new.pdf", pdf_filename="new.pdf",
                    file_size=1, sha256="new")
    with pytest.raises(AlarmLeaseLostError):
        repository.ingest([alarm(2)], now=later, cursor=2, debounce_minutes=10, lease_owner="old")
    assert repository.get_cursor() == 1
    with pytest.raises(AlarmLeaseLostError):
        repository.complete_report(report_id, analysis={}, model_status="model", completed_at=later.isoformat(),
                                   lease_owner="old", lease_now=later, **artifact)
    with pytest.raises(AlarmLeaseLostError):
        repository.fail_report(report_id, "late failure", lease_owner="old", lease_now=later)
    # Omitting ownership is not an escape hatch for an owned report.
    with pytest.raises(AlarmLeaseLostError):
        repository.fail_report(report_id, "legacy caller")
    repository.release_lease("old")
    assert repository.renew_lease("new", later)
    repository.complete_report(report_id, analysis={}, model_status="model", completed_at=later.isoformat(),
                               lease_owner="new", lease_now=later, **artifact)
    assert repository.get_report(report_id)["sha256"] == "new"


def test_same_service_is_non_reentrant_and_long_analysis_renews_lease(repository, monkeypatch):
    entered, finish, renewed = Event(), Event(), Event()
    service = monitor(repository, Source())
    service.LEASE_RENEW_SECONDS = 0.01
    original = repository.renew_lease

    def renew(owner, now, **kwargs):
        result = original(owner, now, **kwargs)
        if entered.is_set():
            renewed.set()
        return result

    monkeypatch.setattr(repository, "renew_lease", renew)

    def analyze(items):
        entered.set()
        assert finish.wait(2)
        return {"facts": []}

    service.analyzer = SimpleNamespace(analyze=analyze)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(service.run_once, NOW)
        try:
            assert entered.wait(1)
            assert service.run_once(NOW)["reason"] == "same_service_running"
            assert renewed.wait(1), "renewal must continue while external analysis blocks"
            competing = monitor(repository, Source())
            assert competing.run_once(NOW)["reason"] == "another_worker"
        finally:
            finish.set()
        assert future.result(timeout=2)["report_status"] == "completed"


def test_attempt_paths_are_isolated_and_failed_pdf_does_not_leave_html(tmp_path):
    class PDF:
        def render(self, html):
            path = html.with_suffix(".pdf")
            path.write_bytes(b"pdf")
            return path

    service = AlarmReportService(replace(settings, project_root=tmp_path), PDF())
    arguments = dict(report_id="rpt", machine_no="Cutter01", analysis={"facts": []}, generated_at=NOW)
    first = service.render(**arguments, execution_id="old")
    second = service.render(**arguments, execution_id="new")
    assert first["html_relative_path"] != second["html_relative_path"]
    service.discard(first)
    assert (service.settings.reports_dir / second["html_relative_path"]).is_file()
    assert (service.settings.reports_dir / second["pdf_relative_path"]).is_file()
    service.pdf_service = SimpleNamespace(render=lambda _: (_ for _ in ()).throw(RuntimeError("pdf unavailable")))
    with pytest.raises(RuntimeError, match="pdf unavailable"):
        service.render(**arguments, execution_id="failed")
    assert not list(service.settings.reports_dir.rglob("*failed*"))


def test_lease_takeover_during_render_cannot_fail_new_attempt(repository):
    discarded = []
    artifact = dict(html_relative_path="old.html", pdf_relative_path="old.pdf",
                    pdf_filename="old.pdf", file_size=1, sha256="old")
    service = monitor(repository, Source())
    later = NOW + timedelta(seconds=601)

    def render(**kwargs):
        assert repository.try_acquire_lease("new-process", later)
        assert repository.recover_interrupted_reports("new-process", later) == 1
        assert reserve(repository, "new-process", later) == kwargs["report_id"]
        return artifact

    service.report_service = SimpleNamespace(render=render, discard=discarded.append)
    with pytest.raises(AlarmLeaseLostError):
        service.run_once(NOW)
    assert discarded == [artifact]
    report = repository.list_reports()[0]
    assert report["status"] == "generating"
    assert report["execution_owner"] == "new-process"
    assert repository.renew_lease("new-process", later)
    assert repository.list_unreported_incidents() == []


def test_recovery_requires_live_lease_and_releases_local_lock_after_error(repository, monkeypatch):
    assert repository.try_acquire_lease("live", NOW)
    reserve(repository, "live")
    with pytest.raises(AlarmLeaseLostError):
        repository.recover_interrupted_reports("outsider", NOW)
    assert repository.get_report("interrupted")["status"] == "generating"
    repository.release_lease("live")
    service = monitor(repository, Source())
    original = repository.try_acquire_lease
    monkeypatch.setattr(repository, "try_acquire_lease", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("database locked")))
    with pytest.raises(RuntimeError):
        service.run_once(NOW)
    monkeypatch.setattr(repository, "try_acquire_lease", original)
    assert service.run_once(NOW)["report_status"] == "completed"


def test_renewal_thread_start_failure_does_not_leak_service_lock_or_lease(repository, monkeypatch):
    service = monitor(repository, Source())
    with monkeypatch.context() as scope:
        scope.setattr("app.services.alarm_monitor_service.threading.Thread.start",
                      lambda _: (_ for _ in ()).throw(RuntimeError("thread capacity exhausted")))
        with pytest.raises(RuntimeError, match="thread capacity exhausted"):
            service.run_once(NOW)
    assert service.run_once(NOW)["report_status"] == "completed"
