from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest

from app.config import settings
from app.database.metadata import MetadataRepository
from app.main import health
from app.workers.report_worker import ReportWorker
from test_report_worker_resilience import IdleMetadata


def health_request(worker):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(worker=worker)),
                           state=SimpleNamespace(request_id="health-test"))


@pytest.mark.parametrize("blocked_worker", ["report", "automation", "email"])
def test_live_but_blocked_consumer_degrades_health_and_recovers(monkeypatch, blocked_worker):
    stamp = [100.0]
    monkeypatch.setattr("app.workers.report_worker.monotonic", lambda: stamp[0])
    entered, release, returned = Event(), Event(), Event()

    def block():
        entered.set()
        release.wait(3)

    class Metadata(IdleMetadata):
        claimed = False

        def claim_next_job(self, now):
            if blocked_worker == "report" and not self.claimed:
                self.claimed = True
                return {"report_id": "blocked"}
            return None

    class Pipeline:
        def run(self, job):
            block()

    class Automation:
        def reconcile_runs(self, now):
            if blocked_worker == "automation":
                block()

        def process_due_schedules(self, now):
            pass

        def process_next_email(self, now):
            if blocked_worker == "email":
                block()
            return False

        def report_completed(self, *args):
            pass

    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01,
                                  worker_stall_timeout_seconds=60), Metadata(), Pipeline(), Automation())
    original = worker._checkpoint

    def checkpoint(name, stage):
        original(name, stage)
        if name == blocked_worker and stage == "idle" and release.is_set():
            returned.set()

    monkeypatch.setattr(worker, "_checkpoint", checkpoint)
    try:
        worker.start()
        assert entered.wait(1)
        stamp[0] += 61
        status = worker.status()
        assert status[f"{blocked_worker}_alive"] is True
        assert blocked_worker in status["stalled_workers"]
        assert health(health_request(worker)).status_code == 503
        release.set()
        assert returned.wait(1)
        assert blocked_worker not in worker.status()["stalled_workers"]
    finally:
        release.set()
        worker.stop()


def test_queue_observation_counts_only_nonterminal_jobs(tmp_path):
    metadata = MetadataRepository(tmp_path / "metadata.db")
    metadata.initialize()
    for index in range(4):
        metadata.create_job(report_id=f"r{index}", report_date="2026-09-30",
                            project_code=None, ship_no=None, request_id=f"req{index}",
                            created_at=f"2026-09-{26 + index}T09:00:00+08:00")
    metadata.update_status("r0", "completed", "done")
    metadata.update_status("r1", "querying", "querying")
    assert metadata.queue_observation() == {
        "queued_count": 2, "running_count": 1,
        "oldest_queued_at": "2026-09-28T09:00:00+08:00",
    }


def test_health_uses_cached_queue_observation_and_db_failure_is_visible(monkeypatch):
    class Metadata(IdleMetadata):
        calls = 0

        def queue_observation(self):
            self.calls += 1
            raise RuntimeError("temporary database failure")

    metadata = Metadata()
    worker = ReportWorker(settings, metadata, SimpleNamespace())
    worker._observe_queue()
    assert worker.status()["queue"]["available"] is False
    worker.status()
    assert metadata.calls == 1, "health reads must not issue blocking database calls"
    worker._observe_queue()
    assert metadata.calls == 1, "database failure observations need a bounded scan interval"


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), 86401])
def test_stall_timeout_rejects_invalid_monitoring_limits(value):
    with pytest.raises(ValueError, match="REPORT_WORKER_STALL_TIMEOUT_SECONDS"):
        replace(settings, worker_stall_timeout_seconds=value)
