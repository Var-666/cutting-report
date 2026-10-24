from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest

from app.services.energy_api import EnergyApiError
from app.workers.energy_history_worker import EnergyHistoryWorker
from test_energy_period_service import NOW, service


def worker_and_clock(tmp_path):
    energy, _ = service(tmp_path)
    now = [NOW]
    energy.clock = lambda: now[0]
    return EnergyHistoryWorker(energy.settings, energy), now


def test_failed_month_does_not_drop_other_months_and_backoff_survives_requests(tmp_path, monkeypatch):
    worker, now = worker_and_clock(tmp_path)
    worker.schedule_range(date(2026, 7, 1), date(2026, 8, 1))
    calls = []

    def sync(month):
        calls.append(month)
        if month.month == 8:
            raise EnergyApiError("platform unavailable")
        return 1

    monkeypatch.setattr(worker.service, "sync_month", sync)
    failed = worker._claim()
    worker._process(failed)
    assert not worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    other = worker._claim()
    assert other["report_month"] == "2026-07-01"
    worker._process(other)
    assert worker._claim() is None
    now[0] += timedelta(seconds=30)
    retry = worker._claim()
    assert retry["report_month"] == failed["report_month"]
    assert retry["attempt_count"] == 2
    assert retry["last_error_code"] == "EnergyApiError"
    assert calls == [date(2026, 8, 1), date(2026, 7, 1)]


def test_restart_recovers_running_claim_and_fences_late_completion(tmp_path):
    worker, now = worker_and_clock(tmp_path)
    worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    old = worker._claim()
    replacement = EnergyHistoryWorker(worker.settings, worker.service)
    replacement.repository.recover_history_queue(replacement.source_key, replacement._now())
    new = replacement._claim()
    assert new["report_month"] == old["report_month"]
    assert new["claim_token"] != old["claim_token"]
    assert not worker.repository.reschedule_history_month(old, worker._now(), worker._now())
    assert replacement.repository.reschedule_history_month(new, replacement._now(), replacement._now())


def test_month_requests_persist_and_atomic_claim_has_one_winner(tmp_path):
    worker, now = worker_and_clock(tmp_path)
    assert worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    second = EnergyHistoryWorker(worker.settings, worker.service)
    assert not second.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda item: item._claim(), [worker, second]))
    assert sum(claim is not None for claim in claims) == 1
    assert second.is_syncing_range(date(2026, 8, 1), date(2026, 8, 31))


def test_successful_partial_fetch_remains_scheduled_without_spinning(tmp_path, monkeypatch):
    worker, now = worker_and_clock(tmp_path)
    worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    monkeypatch.setattr(worker.service, "sync_month", lambda month: 0)
    worker._process(worker._claim())
    assert worker._claim() is None
    assert not worker.is_syncing_range(date(2026, 8, 1), date(2026, 8, 31))
    now[0] += timedelta(seconds=worker.settings.energy_report_snapshot_ttl_seconds)
    assert worker._claim()["report_month"] == "2026-08-01"


def test_completion_write_retry_does_not_repeat_external_query(tmp_path, monkeypatch):
    worker, now = worker_and_clock(tmp_path)
    worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    calls = []
    monkeypatch.setattr(worker.service, "sync_month", lambda month: calls.append(month) or 1)
    original = worker.repository.reschedule_history_month

    def unavailable(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(worker.repository, "reschedule_history_month", unavailable)
    with pytest.raises(RuntimeError):
        worker._process(worker._claim())
    assert worker._pending_finish is not None
    monkeypatch.setattr(worker.repository, "reschedule_history_month", original)
    worker._flush_pending_finish()
    assert worker._pending_finish is None
    assert calls == [date(2026, 8, 1)]
    assert worker._claim() is None


def test_hot_snapshots_share_one_serialized_completion_retry(tmp_path, monkeypatch):
    from threading import Event
    worker, _ = worker_and_clock(tmp_path)
    replacement = EnergyHistoryWorker(worker.settings, worker.service)
    replacement._completion = worker._completion
    worker._pending_finish = ({'report_month': '2026-08-01'}, 'later', 'now', None)
    entered, release = Event(), Event()
    calls = []

    def finish(*args):
        calls.append(args)
        entered.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(worker.repository, 'reschedule_history_month', finish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        old = pool.submit(worker._flush_pending_finish)
        assert entered.wait(1)
        new = pool.submit(replacement._flush_pending_finish)
        release.set()
        old.result(timeout=2)
        new.result(timeout=2)
    assert len(calls) == 1
    assert worker._pending_finish is replacement._pending_finish is None


def test_sampling_configuration_isolates_persisted_queue(tmp_path):
    from dataclasses import replace
    from app.services.energy_period_service import EnergyPeriodService

    worker, _ = worker_and_clock(tmp_path)
    worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    other_service = EnergyPeriodService(
        replace(worker.settings, energy_gas_factor="2"), worker.repository,
        clock=lambda: NOW,
    )
    other = EnergyHistoryWorker(other_service.settings, other_service)
    assert other.source_key != worker.source_key
    assert other._claim() is None


def test_shutdown_before_start_is_safe_and_future_months_not_queued(tmp_path):
    worker, _ = worker_and_clock(tmp_path)
    assert not worker.schedule_range(date(2027, 1, 1), date(2027, 2, 1))
    assert worker._claim() is None
    worker.stop()
    assert worker.stop_event.is_set()


def test_live_worker_survives_claim_failure_and_finishes_later_month(tmp_path, monkeypatch):
    from threading import Event

    worker, _ = worker_and_clock(tmp_path)
    worker.schedule_range(date(2026, 8, 1), date(2026, 8, 1))
    original_claim = worker._claim
    claims = []
    processed = Event()

    def claim():
        claims.append(True)
        if len(claims) == 1:
            raise RuntimeError("temporary database outage")
        return original_claim()

    def sync(month):
        worker.stop_event.set()
        processed.set()
        return 1

    monkeypatch.setattr(worker, "_claim", claim)
    monkeypatch.setattr(worker.service, "sync_month", sync)
    worker.thread.start()
    try:
        assert processed.wait(3), "a claim error must not kill the background queue"
    finally:
        worker.stop()
    assert not worker.thread.is_alive()
    assert not worker.is_syncing_range(date(2026, 8, 1), date(2026, 8, 31))
