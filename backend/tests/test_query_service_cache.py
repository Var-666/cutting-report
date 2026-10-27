from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Event, Lock

import pytest

from app.config import Settings
from app.models.domain import EquipmentRecord, QueryBundle, ReportFilter
from app.services.query_service import EquipmentQueryResult, QueryService


def _service(tmp_path: Path) -> QueryService:
    return QueryService(
        Settings(
            project_root=tmp_path,
            demo_mode=False,
            data_source_mode="sqlserver",
            report_data_cache_seconds=300,
        )
    )


def _equipment_result(task_id: str) -> EquipmentQueryResult:
    return EquipmentQueryResult(
        records=[
            EquipmentRecord(
                task_id=task_id,
                equipment_id="CUT-01",
                project_code="P001",
                ship_no="P001",
                segment_name="S01",
                cut_length=None,
                work_time=None,
                gas_consumption=None,
                electricity_consumption=None,
            )
        ],
        data_updated_at=datetime(2026, 8, 25, 10, 0),
    )


def test_equipment_query_reuses_same_date_and_force_refreshes(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls = 0

    def fetch(_filters: ReportFilter) -> EquipmentQueryResult:
        nonlocal calls
        calls += 1
        return _equipment_result(f"task-{calls}")

    monkeypatch.setattr(service, "_sqlserver_equipment_data", fetch)
    filters = ReportFilter(report_date=date(2026, 8, 25))

    first = service.fetch_equipment_data(filters)
    second = service.fetch_equipment_data(filters)
    refreshed = service.fetch_equipment_data(filters, force=True)

    assert calls == 2
    assert second is first
    assert refreshed.records[0].task_id == "task-2"


def test_latest_date_query_is_shared_and_can_be_refreshed(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls = 0

    def fetch(_on_or_before: date) -> date:
        nonlocal calls
        calls += 1
        return date(2026, 8, 24 + calls)

    monkeypatch.setattr(service, "_latest_production_date_uncached", fetch)
    selected = date(2026, 8, 25)

    assert service.latest_production_date(selected) == date(2026, 8, 25)
    assert service.latest_production_date(selected) == date(2026, 8, 25)
    assert service.latest_production_date(selected, force=True) == date(2026, 8, 26)
    assert calls == 2


def test_sqlserver_equipment_fetch_executes_only_equipment_query(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []
    parameters_seen: list[tuple] = []
    updated_at = datetime(2026, 8, 25, 12, 0)

    def fetch_all(sql: str, parameters: tuple) -> list[dict]:
        calls.append(sql)
        parameters_seen.append(parameters)
        return [
            {
                "task_id": "task-1",
                "equipment_id": "CUT-01",
                "project_code": "P001",
                "ship_no": "P001",
                "segment_name": "S01",
                "cut_length": "10.125",
                "work_time": "60",
                "production_time": datetime(2026, 8, 25, 8, 0),
                "production_end_time": updated_at,
                "source_updated_at": updated_at,
                "gas_consumption": None,
                "electricity_consumption": None,
                "remark": None,
            }
        ]

    monkeypatch.setattr(service.client, "fetch_all", fetch_all)

    result = service.fetch_equipment_data(
        ReportFilter(report_date=date(2026, 8, 25))
    )

    assert len(calls) == 1
    assert "MACHINE_NO" in calls[0]
    assert parameters_seen[0][0] == "Cutter01"
    assert result.records[0].task_id == "task-1"
    assert result.records[0].production_end_time == updated_at
    assert result.data_updated_at == updated_at


def test_summary_and_workshop_ranges_each_execute_one_cached_query(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []
    updated_at = datetime(2026, 8, 25, 12, 0)

    def fetch_all(sql: str, _parameters: tuple) -> list[dict]:
        calls.append(sql)
        return [
            {
                "task_id": "task-1",
                "project_code": "P001",
                "ship_no": "P001",
                "segment_name": "S01",
                "program_no": "OP-1",
                "cut_length": "10",
                "groove_length": "1",
                "work_time": "60",
                "production_time": datetime(2026, 8, 25, 8, 0),
                "source_updated_at": updated_at,
                "remark": None,
            }
        ]

    monkeypatch.setattr(service.client, "fetch_all", fetch_all)
    filters = ReportFilter(report_date=date(2026, 8, 25))

    summary = service.fetch_summary_data(filters)
    workshop = service.fetch_workshop_data(filters)
    assert service.fetch_summary_data(filters) is summary
    assert service.fetch_workshop_data(filters) is workshop

    assert len(calls) == 2
    assert len(summary.summary_records) == 1
    assert len(workshop.workshop_records) == 1


def test_equipment_cache_and_query_locks_are_bounded(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        service,
        "_sqlserver_equipment_data",
        lambda _filters: EquipmentQueryResult([]),
    )

    for offset in range(140):
        service.fetch_equipment_data(ReportFilter(report_date=date(2025, 1, 1)))
        service.fetch_equipment_data(
            ReportFilter(
                report_date=date.fromordinal(date(2025, 1, 1).toordinal() + offset)
            )
        )

    assert len(service._equipment_cache._entries) <= 128
    assert len(service._equipment_cache._query_locks) == 32


def test_equipment_monthly_metrics_reuse_the_range_query(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    calls = []
    monkeypatch.setattr(
        service.client,
        "fetch_all",
        lambda sql, parameters: calls.append((sql, parameters)) or [],
    )
    start = date(2025, 10, 1)
    end = date(2026, 9, 13)

    first = service.fetch_equipment_range_metrics(start, end)
    second = service.fetch_equipment_range_metrics(start, end)

    assert first is second
    assert len(calls) == 1


CACHE_KINDS = ("summary", "workshop", "equipment")


def _cache_result(kind, version):
    if kind == "equipment":
        return _equipment_result(f"task-{version}")
    return QueryBundle([], [], [], datetime(2026, 8, 25) + timedelta(seconds=version))


@pytest.mark.parametrize("kind", CACHE_KINDS)
def test_query_cache_expiry_and_force_refresh_keep_the_latest_result(tmp_path, monkeypatch, kind):
    service = _service(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr("app.services.query_service.monotonic", lambda: clock[0])
    monkeypatch.setattr("app.services.query_cache.monotonic", lambda: clock[0])
    calls = []

    def load(filters):
        calls.append(filters)
        return _cache_result(kind, len(calls))

    monkeypatch.setattr(service, f"_sqlserver_{kind}_data", load)
    read = getattr(service, f"fetch_{kind}_data")
    filters = ReportFilter(report_date=date(2026, 8, 25))
    first = read(filters)
    clock[0] += 299
    assert read(filters) is first
    clock[0] += 1
    expired = read(filters)
    assert expired is not first
    clock[0] += 1
    refreshed = read(filters, force=True)
    assert refreshed is not expired
    assert read(filters) is refreshed
    assert len(calls) == 3


@pytest.mark.parametrize("kind", CACHE_KINDS)
def test_zero_ttl_keeps_queries_uncached(tmp_path, monkeypatch, kind):
    service = _service(tmp_path)
    service.settings = replace(service.settings, report_data_cache_seconds=0)
    calls = []

    def load(filters):
        calls.append(filters)
        return _cache_result(kind, len(calls))

    monkeypatch.setattr(service, f"_sqlserver_{kind}_data", load)
    read = getattr(service, f"fetch_{kind}_data")
    filters = ReportFilter(report_date=date(2026, 8, 25))
    assert read(filters) is not read(filters)
    assert len(calls) == 2
    cache = service._equipment_cache if kind == "equipment" else service._bundle_cache
    assert cache._entries == {}


@pytest.mark.parametrize("kind", CACHE_KINDS)
def test_cache_keys_isolate_date_ranges_projects_and_ships(tmp_path, monkeypatch, kind):
    service = _service(tmp_path)
    calls = []

    def load(filters):
        calls.append(filters)
        return _cache_result(kind, len(calls))

    monkeypatch.setattr(service, f"_sqlserver_{kind}_data", load)
    read = getattr(service, f"fetch_{kind}_data")
    day = date(2026, 8, 25)
    filters = [ReportFilter(report_date=day),
               ReportFilter(report_date=day, project_code="P001"),
               ReportFilter(report_date=day, ship_no="S001"),
               ReportFilter(report_date=day, period_start=day, period_end=day + timedelta(days=1))]
    results = [read(item) for item in filters]
    assert len({id(result) for result in results}) == len(filters)
    for item, result in zip(filters, results):
        assert read(replace(item)) is result
    assert len(calls) == len(filters)


@pytest.mark.parametrize("kind", CACHE_KINDS)
@pytest.mark.parametrize("force_waiter", [False, True])
def test_concurrent_reads_share_the_query_already_started(tmp_path, monkeypatch, kind, force_waiter):
    service = _service(tmp_path)
    entered, release, waiting = Event(), Event(), Event()
    calls = []
    filters = ReportFilter(report_date=date(2026, 8, 25))
    cache = service._equipment_cache if kind == "equipment" else service._bundle_cache
    key = service._cache_key(kind, filters)
    index = hash(key) % len(cache._query_locks)
    original_lock = cache._query_locks[index]

    class ObservedLock:
        """Start completion only once both requests have reached the query gate."""
        attempts = 0
        guard = Lock()

        def __enter__(self):
            with self.guard:
                self.attempts += 1
                if self.attempts == 2:
                    waiting.set()
            original_lock.acquire()

        def __exit__(self, *args):
            original_lock.release()

    cache._query_locks[index] = ObservedLock()

    def load(_filters):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return _cache_result(kind, len(calls))

    monkeypatch.setattr(service, f"_sqlserver_{kind}_data", load)
    read = getattr(service, f"fetch_{kind}_data")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read, filters)
        try:
            assert entered.wait(2)
            second = pool.submit(read, filters, force=force_waiter)
            assert waiting.wait(2)
        finally:
            release.set()
        result = first.result(timeout=2)
        assert second.result(timeout=2) is result
    assert len(calls) == 1
    assert read(filters, force=True) is not result  # a later explicit refresh still runs
    assert len(calls) == 2


@pytest.mark.parametrize("kind", CACHE_KINDS)
def test_failed_refresh_does_not_replace_good_data_and_can_retry(tmp_path, monkeypatch, kind):
    service = _service(tmp_path)
    calls = []

    def load(_filters):
        calls.append(len(calls) + 1)
        if len(calls) == 2:
            raise OSError("temporary query failure")
        return _cache_result(kind, len(calls))

    monkeypatch.setattr(service, f"_sqlserver_{kind}_data", load)
    read = getattr(service, f"fetch_{kind}_data")
    filters = ReportFilter(report_date=date(2026, 8, 25))
    first = read(filters)
    with pytest.raises(OSError, match="temporary query failure"):
        read(filters, force=True)
    assert read(filters) is first
    refreshed = read(filters, force=True)
    assert refreshed is not first
    assert read(filters) is refreshed
    assert len(calls) == 3


def test_equipment_and_bundle_caches_keep_independent_capacities(tmp_path, monkeypatch):
    service = _service(tmp_path)
    for kind in CACHE_KINDS:
        monkeypatch.setattr(service, f"_sqlserver_{kind}_data", lambda _filters, kind=kind: _cache_result(kind, 1))
    filters = ReportFilter(report_date=date(2026, 8, 25))
    summary = service.fetch_summary_data(filters)
    workshop = service.fetch_workshop_data(filters)
    for offset in range(140):
        service.fetch_equipment_data(ReportFilter(report_date=date(2025, 1, 1) + timedelta(days=offset)))
    assert len(service._equipment_cache._entries) == 128
    assert len(service._equipment_cache._query_locks) == 32
    assert service.fetch_summary_data(filters) is summary
    assert service.fetch_workshop_data(filters) is workshop
    assert len(service._bundle_cache._entries) == 2
    for offset in range(140):
        service.fetch_summary_data(ReportFilter(report_date=date(2025, 1, 1) + timedelta(days=offset)))
    assert len(service._bundle_cache._entries) == 128
    assert len(service._bundle_cache._query_locks) == 32
