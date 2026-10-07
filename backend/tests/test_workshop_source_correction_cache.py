from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event
import pytest

from app.config import Settings
from app.models.domain import QueryBundle, ReportFilter
from app.services.query_service import EquipmentQueryResult, QueryService
from test_workshop_snapshot_sync import record


def test_explicit_correction_invalidates_warmup_and_ranges_but_preserves_other_queries(tmp_path, monkeypatch):
    service = QueryService(Settings(project_root=tmp_path, data_source_mode="sqlserver", report_data_cache_seconds=300))
    selected_date = date(2026, 9, 30)
    rows = [record("A")]
    calls = []

    def workshop(filters):
        calls.append("workshop")
        return QueryBundle([], list(rows), [])

    def summary(filters):
        calls.append("summary")
        return QueryBundle([], [], [])

    monkeypatch.setattr(service, "_sqlserver_workshop_data", workshop)
    monkeypatch.setattr(service, "_sqlserver_summary_data", summary)
    filters = ReportFilter(report_date=selected_date, period_start=selected_date, period_end=date(2026, 10, 1))
    service.prepare_workshop_data(selected_date)
    service.fetch_workshop_data(filters)
    service.fetch_summary_data(filters)
    rows[:] = [record("A", work=90)]
    service.invalidate_workshop_data()
    assert service.prepare_workshop_data(selected_date).workshop_records[0].work_time == 90
    assert service.fetch_workshop_data(filters).workshop_records[0].work_time == 90
    service.fetch_summary_data(filters)
    assert calls == ["workshop", "workshop", "summary", "workshop", "workshop"]


@pytest.mark.parametrize("warmup", [False, True])
def test_pre_correction_inflight_read_cannot_repopulate_stale_cache(tmp_path, monkeypatch, warmup):
    service = QueryService(Settings(project_root=tmp_path, data_source_mode="sqlserver", report_data_cache_seconds=300))
    entered, release = Event(), Event()
    calls = 0

    def load(filters):
        nonlocal calls
        calls += 1
        result = QueryBundle([], [record("A", work=60 if calls == 1 else 90)], [])
        if calls == 1:
            entered.set()
            assert release.wait(2)
        return result

    monkeypatch.setattr(service, "_sqlserver_workshop_data", load)
    filters = ReportFilter(report_date=date(2026, 9, 30))
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = (pool.submit(service.prepare_workshop_data, filters.report_date) if warmup
                   else pool.submit(service.fetch_workshop_data, filters))
        try:
            assert entered.wait(1)
            invalidated = pool.submit(service.invalidate_workshop_data)
            invalidated.result(timeout=0.5)
        finally:
            release.set()
        assert pending.result(timeout=1).workshop_records[0].work_time == 60
    refreshed = (service.prepare_workshop_data(filters.report_date) if warmup
                 else service.fetch_workshop_data(filters))
    assert refreshed.workshop_records[0].work_time == 90
    assert calls == 2


@pytest.mark.parametrize("kind", ["summary", "equipment"])
def test_workshop_correction_does_not_discard_other_inflight_query_results(tmp_path, monkeypatch, kind):
    service = QueryService(Settings(project_root=tmp_path, data_source_mode="sqlserver", report_data_cache_seconds=300))
    entered, release = Event(), Event()
    calls = []
    expected = EquipmentQueryResult([]) if kind == "equipment" else QueryBundle([], [], [])

    def load(filters):
        calls.append(filters)
        entered.set()
        assert release.wait(5)
        return expected

    monkeypatch.setattr(service, f"_sqlserver_{kind}_data", load)
    read = getattr(service, f"fetch_{kind}_data")
    filters = ReportFilter(report_date=date(2026, 9, 30))
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(read, filters)
        try:
            assert entered.wait(2)
            # Correction must finish while the unrelated source is still blocked.
            pool.submit(service.invalidate_workshop_data).result(timeout=0.5)
        finally:
            release.set()
        assert pending.result(timeout=2) is expected
    assert read(filters) is expected
    assert len(calls) == 1
