from dataclasses import replace
from threading import Event
from types import SimpleNamespace
import json

import anyio
import pytest

from app.config import settings
from app.workers.report_worker import ReportWorker


def test_claim_and_failure_record_errors_do_not_kill_report_worker():
    finished = Event()
    calls = []

    class Metadata:
        def queue_observation(self):
            return {"queued_count": 0, "oldest_queued_at": None, "running_count": 0}

        def recover_interrupted_jobs(self):
            pass

        def claim_next_job(self, now):
            calls.append(now)
            if len(calls) == 1:
                raise RuntimeError("temporary database failure")
            if len(calls) <= 3:
                return {"report_id": str(len(calls))}
            return None

        def update_status(self, *args, **kwargs):
            raise RuntimeError("failure status write unavailable")

    class Pipeline:
        def run(self, job):
            if job["report_id"] == "2":
                raise RuntimeError("bad report")
            finished.set()

    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01), Metadata(), Pipeline())
    try:
        worker.start()
        assert finished.wait(2), "one broken report must not stop later reports"
        assert worker.thread.is_alive()
    finally:
        worker.stop()


def test_slow_report_cannot_starve_email_and_callback_cannot_fail_completed_report():
    generating, release, emailed, completed = (Event() for _ in range(4))
    failed = []

    class Metadata:
        claimed = False

        def queue_observation(self):
            return {"queued_count": 0, "oldest_queued_at": None, "running_count": 0}

        def recover_interrupted_jobs(self):
            pass

        def claim_next_job(self, now):
            if self.claimed:
                return None
            self.claimed = True
            return {"report_id": "rpt_slow"}

        def update_status(self, *args, **kwargs):
            failed.append(args)

    class Pipeline:
        def run(self, job):
            generating.set()
            assert release.wait(3)

    class Automation:
        def reconcile_runs(self, now):
            pass

        def process_due_schedules(self, now):
            pass

        def process_next_email(self, now):
            if generating.is_set():
                emailed.set()
            return False

        def report_completed(self, report_id, now):
            completed.set()
            raise RuntimeError("outbox unavailable")

        def report_failed(self, *args):
            failed.append(args)

    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01), Metadata(), Pipeline(), Automation())
    try:
        worker.start()
        assert generating.wait(1)
        assert emailed.wait(1), "email must proceed while report generation is blocked"
        release.set()
        assert completed.wait(1)
    finally:
        release.set()
        worker.stop()
    assert not failed, "a delivery callback failure must not corrupt a completed report"


@pytest.mark.parametrize("stopped", ["report_alive", "automation_alive", "email_alive"])
def test_health_is_degraded_when_background_thread_stopped(stopped):
    from app.main import health

    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(worker_status_reader=SimpleNamespace(
            status=lambda: {key: key != stopped for key in (
                "report_alive", "automation_alive", "email_alive"
            )}
        ))),
        state=SimpleNamespace(request_id="test-health"),
    )
    response = anyio.run(health, request)
    assert response["success"] is True
    assert response["data"]["status"] == "ok"
    assert response["data"]["background_ready"] is False
    assert response["data"]["workers"][stopped] is False


class IdleMetadata:
    def queue_observation(self):
        return {"queued_count": 0, "oldest_queued_at": None, "running_count": 0}

    def recover_interrupted_jobs(self):
        pass

    def claim_next_job(self, now):
        return None


@pytest.mark.parametrize("phase", ["recovery", "schedule"])
def test_slow_scheduler_preparation_cannot_starve_email(phase):
    blocked, release, emailed = (Event() for _ in range(3))

    class Automation:
        def _prepare(self):
            blocked.set()
            release.wait(3)

        def reconcile_runs(self, now):
            if phase == "recovery":
                self._prepare()

        def process_due_schedules(self, now):
            if phase == "schedule":
                self._prepare()

        def process_next_email(self, now):
            if blocked.is_set():
                emailed.set()
            return False

    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01), IdleMetadata(),
                          SimpleNamespace(), Automation())
    try:
        worker.start()
        assert blocked.wait(1)
        assert emailed.wait(1), "persisted email must proceed during slow source preparation"
        assert worker.status()["email_alive"] is True
    finally:
        release.set()
        worker.stop()
    assert all(not thread.is_alive() for thread in worker._threads())


def test_slow_smtp_cannot_starve_next_schedule_scan():
    sending, release, scanned_again = (Event() for _ in range(3))

    class Automation:
        def reconcile_runs(self, now):
            pass

        def process_due_schedules(self, now):
            if sending.is_set():
                scanned_again.set()

        def process_next_email(self, now):
            sending.set()
            release.wait(3)
            return False

    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01, automation_scan_seconds=0.01),
                          IdleMetadata(), SimpleNamespace(), Automation())
    try:
        worker.start()
        assert sending.wait(1)
        assert scanned_again.wait(1), "a blocked SMTP exchange must not delay schedule scans"
    finally:
        release.set()
        worker.stop()


def test_email_consumer_recovers_after_one_claim_error():
    recovered = Event()
    calls = []

    class Automation:
        def reconcile_runs(self, now):
            pass

        def process_due_schedules(self, now):
            pass

        def process_next_email(self, now):
            calls.append(now)
            if len(calls) == 1:
                raise RuntimeError("temporary outbox fault")
            recovered.set()
            return False

    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01), IdleMetadata(),
                          SimpleNamespace(), Automation())
    try:
        worker.start()
        assert recovered.wait(1), "one outbox fault must not permanently stop delivery"
        assert worker.email_thread.is_alive()
    finally:
        worker.stop()


def test_partial_thread_start_failure_stops_already_started_workers(monkeypatch):
    automation = SimpleNamespace(reconcile_runs=lambda now: None,
                                 process_due_schedules=lambda now: None)
    worker = ReportWorker(replace(settings, worker_poll_seconds=0.01), IdleMetadata(),
                          SimpleNamespace(), automation)

    def fail_start():
        raise RuntimeError("cannot start mail consumer")

    monkeypatch.setattr(worker.email_thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="cannot start mail consumer"):
        worker.start()
    assert worker.stop_event.is_set()
    assert all(not thread.is_alive() for thread in worker._threads())
    worker.stop()  # An unstarted thread must not make cleanup raise.
