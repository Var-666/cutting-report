import pytest
import json
from datetime import date, datetime
from threading import Event, Lock
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.database.llm_usage import LlmUsageRepository
from app.database.metadata import MetadataRepository
from app.services.report_analysis_service import ReportAnalysisService
from app.workers.current_report_refresh_worker import CurrentReportRefreshWorker
from app.workers.leadership_refresh_worker import LeadershipRefreshWorker
from app.workers.alarm_worker import AlarmWorker
from app.workers.parallel_analysis import ParallelAnalysisTasks
from app.services.llm_service import _LlmCapacity, llm_capacity


TODAY = date(2026, 9, 28)


@pytest.mark.parametrize("limit", [4, 10])
def test_periodic_analysis_can_use_shared_capacity_beyond_background_quota_and_releases_it(monkeypatch, limit):
    capacity = _LlmCapacity()
    monkeypatch.setattr("app.services.llm_service._LLM_CAPACITY", capacity)
    settings = SimpleNamespace(llm_max_concurrent=limit, llm_queue_timeout_seconds=2)
    entered, release = Event(), Event()
    lock = Lock()
    calls = []
    runner = ParallelAnalysisTasks()
    def analyze():
        with llm_capacity(settings):
            with lock:
                calls.append(1)
                if len(calls) == limit:
                    entered.set()
            assert release.wait(3)
    try:
        for _ in range(limit):
            runner.start(analyze)
        assert entered.wait(1), "fixed-cadence analysis must be able to use the whole shared model pool"
        assert capacity._active == limit
    finally:
        release.set()
        runner.stop()
    assert capacity._active == capacity._background_active == 0
    runner.start(analyze)
    assert len(calls) == limit, "shutdown must reject new scheduled model requests"


@pytest.mark.parametrize("device_id", ["Cutter01", "Cutter02"])
@pytest.mark.parametrize("report_type", ["leadership", "workshop", "equipment"])
def test_unchanged_overlapping_periodic_rounds_each_send_http_and_record_usage(tmp_path, monkeypatch, device_id, report_type):
    settings = Settings(project_root=tmp_path, device_id=device_id, llm_enabled=True,
                        llm_api_key="test", llm_base_url="http://llm.test", llm_max_concurrent=4)
    metadata = MetadataRepository(settings.metadata_db_path, device_id=device_id)
    metadata.initialize()
    service = ReportAnalysisService(settings, metadata)
    entered, second_entered, release, completed = Event(), Event(), Event(), Event()
    lock = Lock()
    requests, results = [], []
    context = {"report_date": TODAY.isoformat(), "facts": [], "tables": []}
    analysis_date = TODAY.strftime('%Y-%m') if report_type == 'leadership' else TODAY.isoformat()

    def transport(request):
        with lock:
            requests.append(json.loads(request.content))
            number = len(requests)
        if number == 1:
            entered.set()
            assert release.wait(3)
        else:
            second_entered.set()
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps({key: "分析" for key in
                ("overview", "features", "alerts", "suggestions")})}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
        })

    original_client = httpx.Client
    monkeypatch.setattr("app.services.llm_service.httpx.Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(transport), **kwargs))

    def analyze(*args, force=False, **kwargs):
        result = service._analyze(report_type=report_type, report_date=analysis_date,
                                  context=context, force=force, publish_latest=kwargs.get('publish_latest', False))
        with lock:
            results.append(result)
            if len(results) == 2:
                completed.set()

    analysis = SimpleNamespace(analyze_leadership=analyze, analyze_workshop=analyze, analyze_equipment=analyze)
    if report_type == "leadership":
        worker = LeadershipRefreshWorker(settings,
            SimpleNamespace(refresh=lambda *a, **kw: 1, fetch=lambda *a: "same report"), analysis)
        worker._now = lambda: datetime.combine(TODAY, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
        schedule = lambda: worker._refresh(TODAY.replace(day=1), False)
    else:
        worker = CurrentReportRefreshWorker(settings,
            SimpleNamespace(fetch_equipment_data=lambda *a: SimpleNamespace(records=[])),
            SimpleNamespace(workshop_records=lambda *a: (None, [])),
            SimpleNamespace(filter_task_records=lambda rows: rows, get_monthly_range=lambda *a: {"rows": []}), analysis)
        args = (TODAY,) if report_type == "workshop" else (TODAY, [], {"summary": {}, "units": {}, "records": []})
        schedule = lambda: worker._schedule_analysis(report_type, args)
    worker.analysis_thread.start()
    try:
        schedule()
        assert entered.wait(1)
        schedule()
        assert second_entered.wait(1), "a new periodic round must send HTTP before the old round finishes"
        release.set()
        assert completed.wait(1)
    finally:
        release.set()
        worker.stop()
    assert len(requests) == 2
    assert requests[0] == requests[1], "unchanged input must not suppress the next scheduled model call"
    assert all(not result.cached for result in results)
    assert LlmUsageRepository(settings.metadata_db_path).summary() == {
        "call_count": 2, "metered_count": 2, "total_tokens": 18,
    }
    cached = service._analyze(report_type=report_type, report_date=analysis_date, context=context, force=False)
    assert cached.cached
    assert len(requests) == 2, "ordinary page reads must reuse the refreshed result"
    assert service.latest_default_analysis(report_type, TODAY) is not None


@pytest.mark.parametrize("kind", ["current", "leadership"])
def test_refresh_schedule_starts_new_round_at_300_seconds(kind, monkeypatch):
    settings = SimpleNamespace(report_background_refresh_seconds=300, timezone="Asia/Shanghai",
                               leadership_report_reconcile_hour=2)
    tick = [0.0]
    rounds = []
    if kind == "current":
        worker = CurrentReportRefreshWorker(settings, None, None, None, None)
        monkeypatch.setattr("app.workers.current_report_refresh_worker.monotonic", lambda: tick[0])
        def run_once(today):
            rounds.append(tick[0])
            tick[0] += 7  # Data preparation time must not extend the five-minute interval.
        def wait(delay):
            tick[0] += delay
        worker.run_once = run_once
        worker.stop_event = SimpleNamespace(is_set=lambda: len(rounds) >= 2, wait=wait)
        worker._run_loop()
    else:
        worker = LeadershipRefreshWorker(settings, None)
        worker._now = lambda: datetime.combine(TODAY, datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai"))
        worker._last_reconcile_date = TODAY
        monkeypatch.setattr("app.workers.leadership_refresh_worker.monotonic", lambda: tick[0])
        worker.schedule = lambda *a, **kw: rounds.append(tick[0])
        for tick[0] in (0.0, 299.0, 300.0):
            worker._schedule_periodic_work()
    assert rounds == [0.0, 300.0]


def test_current_reports_force_model_analysis_each_round_without_a_browser() -> None:
    calls = []

    class Query:
        def latest_production_date(self, day, *, force=False):
            calls.append(("latest", day, force))
            return TODAY

        def fetch_workshop_data(self, filters, *, force=False):
            calls.append(("workshop_query", filters.report_date, force))

        def fetch_equipment_data(self, filters, *, force=False):
            calls.append(("equipment_query", filters.report_date, force))
            return SimpleNamespace(records=[])

    class Range:
        def workshop_records(self, start, end):
            calls.append(("workshop_records", start, end))
            return None, []

    class Energy:
        def get_task_day(self, selected, records, *, force=False):
            calls.append(("task_energy", selected, force))
            return {
                "report_date": selected.isoformat(), "records": [],
                "summary": {"energy_data_mode": "unavailable"}, "units": {},
            }

        def get_day(self, selected, *, force=False):
            calls.append(("energy_day", selected, force))

        def filter_task_records(self, records):
            return records

    class Analysis:
        def analyze_workshop(self, report_date, records, **kwargs):
            assert kwargs['publish_latest']
            calls.append(("workshop_analysis", report_date, kwargs["force"]))

        def analyze_equipment(self, report_date, records, **kwargs):
            assert kwargs['publish_latest']
            calls.append(("equipment_analysis", report_date, kwargs["force"]))

    worker = CurrentReportRefreshWorker(
        SimpleNamespace(llm_enabled=True, effective_data_source_mode="sqlserver"),
        Query(), Range(), Energy(), Analysis(),
    )
    worker.run_once(TODAY)
    worker.run_once(TODAY)

    assert calls.count(("workshop_query", TODAY, True)) == 2
    assert calls.count(("equipment_query", TODAY, True)) == 2
    assert calls.count(("task_energy", TODAY, True)) == 2
    assert calls.count(("energy_day", TODAY, True)) == 2
    assert not any("analysis" in call[0] for call in calls)
    assert [kind for kind, _args in worker._analysis_pending] == ["workshop", "equipment"] * 2
    for kind, args in worker._analysis_pending:
        getattr(worker, f"_analyze_{kind}")(*args)
    assert calls.count(("workshop_analysis", TODAY.isoformat(), True)) == 2
    assert calls.count(("equipment_analysis", TODAY.isoformat(), True)) == 2


def test_leadership_refresh_schedules_current_month_analysis_if_unchanged() -> None:
    calls = []
    month = TODAY.replace(day=1)

    class Leadership:
        def refresh(self, selected, *, bootstrap=False):
            calls.append(("refresh", selected, bootstrap))
            return 1

        def fetch(self, selected):
            calls.append(("fetch", selected))
            return "report"

    class Analysis:
        def analyze_leadership(self, report, *, force=False, publish_latest=False):
            assert publish_latest
            calls.append(("analysis", report, force))

    worker = LeadershipRefreshWorker(
        SimpleNamespace(llm_enabled=True), Leadership(), Analysis()
    )
    worker._now = lambda: datetime(2026, 9, 28, tzinfo=ZoneInfo("Asia/Shanghai"))
    worker._refresh(month, False)
    worker._refresh(month, False)

    assert calls.count(("analysis", "report", False)) == 0
    assert list(worker._analysis_pending) == [month, month]
    assert calls.count(("refresh", month, False)) == 2


def test_daily_energy_snapshot_failure_does_not_skip_task_analysis() -> None:
    calls = []

    class Query:
        def fetch_equipment_data(self, _filters, *, force=False):
            return SimpleNamespace(records=[])

    class Energy:
        def get_task_day(self, selected, records, *, force=False):
            return {"report_date": selected.isoformat(), "records": [],
                    "summary": {}, "units": {}}

        def get_day(self, selected, *, force=False):
            raise RuntimeError("meter unavailable")

        def filter_task_records(self, records):
            return records

    class Analysis:
        def analyze_equipment(self, report_date, records, **kwargs):
            calls.append((report_date, kwargs["force"]))

    worker = CurrentReportRefreshWorker(
        SimpleNamespace(llm_enabled=True), Query(), None, Energy(), Analysis(),
    )
    worker._refresh_equipment(TODAY)
    assert not calls
    worker._analyze_equipment(*worker._analysis_pending[0][1])
    assert calls == [(TODAY.isoformat(), True)]


def test_automatic_report_and_alarm_consumers_use_background_capacity_then_restore_foreground(monkeypatch):
    class Capacity(_LlmCapacity):
        def acquire(self, settings, *, background):
            calls.append(background)
            return super().acquire(settings, background=background)

    calls = []
    monkeypatch.setattr('app.services.llm_service._LLM_CAPACITY', Capacity())
    settings = SimpleNamespace(llm_enabled=True, llm_max_concurrent=4, llm_queue_timeout_seconds=1,
                               timezone='Asia/Shanghai', alarm_monitor_interval_seconds=300)

    def analyze(*args, **kwargs):
        with llm_capacity(settings):
            pass

    current = CurrentReportRefreshWorker(settings, SimpleNamespace(latest_production_date=lambda *a, **k: TODAY),
                                         None, None, None)
    monkeypatch.setattr(current, '_refresh_workshop', analyze)
    monkeypatch.setattr(current, '_refresh_equipment', analyze)
    current.run_once(TODAY)

    leadership = LeadershipRefreshWorker(settings, SimpleNamespace(refresh=lambda *a, **k: 1, fetch=lambda *a: 'report'),
                                         SimpleNamespace(analyze_leadership=analyze))
    leadership._now = lambda: datetime(2026, 9, 28, tzinfo=ZoneInfo('Asia/Shanghai'))
    leadership._refresh(TODAY.replace(day=1), False)

    alarm = AlarmWorker(settings, None)

    def synchronize(*args):
        analyze()
        alarm.stop_event.set()

    alarm.service = SimpleNamespace(run_once=synchronize)
    alarm._run_loop()
    analyze()
    assert list(leadership._analysis_pending) == [TODAY.replace(day=1)]
    assert calls == [True, True, True, False]


def test_slow_leadership_analysis_does_not_delay_or_merge_later_rounds():
    from threading import Event
    entered, release, updated = Event(), Event(), Event()
    calls = []
    revision = [0]

    class Source:
        def refresh(self, month, *, bootstrap=False):
            revision[0] += 1
            return 1
        def fetch(self, month):
            return revision[0]

    class Analysis:
        def analyze_leadership(self, report, *, force=False, publish_latest=False):
            assert publish_latest
            assert force
            calls.append(report)
            if len(calls) == 1:
                entered.set()
                assert release.wait(3)
            elif len(calls) == 6:
                updated.set()

    worker = LeadershipRefreshWorker(SimpleNamespace(llm_enabled=True), Source(), Analysis())
    worker._now = lambda: datetime(2026, 9, 28, tzinfo=ZoneInfo('Asia/Shanghai'))
    worker.analysis_thread.start()
    try:
        worker._refresh(TODAY.replace(day=1), False)
        assert entered.wait(1)
        for _ in range(5):
            worker._refresh(TODAY.replace(day=1), False)
        assert revision[0] == 6
        assert updated.wait(1)
        assert len(calls) == 6, "all later rounds must start while the first is blocked"
    finally:
        release.set()
        worker.stop()
    assert len(calls) == 6
    assert not worker.analysis_thread.is_alive()


@pytest.mark.parametrize("blocked_type", ["workshop", "equipment"])
def test_current_slow_analysis_preserves_every_round_and_its_equipment_inputs(monkeypatch, blocked_type):
    from threading import Event
    entered, release, completed = Event(), Event(), Event()
    revisions = {"workshop": 0, "equipment": 0, "energy": 0}
    calls = []

    class Query:
        def latest_production_date(self, today, *, force=False): return today
        def fetch_workshop_data(self, filters, *, force=False):
            assert force
            revisions["workshop"] += 1
        def fetch_equipment_data(self, filters, *, force=False):
            assert force
            revisions["equipment"] += 1
            return SimpleNamespace(records=[revisions["equipment"]])

    class Energy:
        def get_task_day(self, day, records, *, force=False):
            assert force
            return {"revision": records[0]}
        def get_day(self, day, *, force=False):
            assert force
            revisions["energy"] += 1

    worker = CurrentReportRefreshWorker(SimpleNamespace(llm_enabled=True), Query(), None, Energy(), None)

    def workshop(day):
        analyze("workshop", day)
    def equipment(day, records, snapshot):
        assert records[0] == snapshot["revision"]
        analyze("equipment", day, records[0])
    def analyze(kind, day, revision=None):
        calls.append((kind, day, revision))
        if kind == blocked_type and not entered.is_set():
            entered.set()
            assert release.wait(3)
        if len(calls) == 12:
            completed.set()

    monkeypatch.setattr(worker, "_analyze_workshop", workshop)
    monkeypatch.setattr(worker, "_analyze_equipment", equipment)
    worker.run_once(TODAY)
    worker.analysis_thread.start()
    try:
        assert entered.wait(1)
        latest = date(2026, 9, 29)
        for _ in range(5):
            worker.run_once(latest)
        assert revisions == {"workshop": 6, "equipment": 6, "energy": 6}
        assert completed.wait(1)
        assert len(calls) == 12, "later rounds must start before the blocked analysis finishes"
    finally:
        release.set()
        worker.stop()
    assert ("equipment", latest, 6) in calls
    assert ("workshop", latest, None) in calls
    assert sorted(revision for kind, _day, revision in calls if kind == "equipment") == list(range(1, 7))
    assert not worker._analysis_pending
    assert not worker.analysis_thread.is_alive()


def test_current_analysis_failure_does_not_skip_other_type_or_later_work(monkeypatch):
    from threading import Event
    equipment_done, recovered = Event(), Event()
    worker = CurrentReportRefreshWorker(SimpleNamespace(llm_enabled=True), None, None, None, None)
    calls = []
    def workshop(day):
        calls.append(day)
        if len(calls) == 1:
            raise RuntimeError("model unavailable")
        recovered.set()
    monkeypatch.setattr(worker, "_analyze_workshop", workshop)
    monkeypatch.setattr(worker, "_analyze_equipment", lambda *args: equipment_done.set())
    worker._schedule_analysis("workshop", (TODAY,))
    worker._schedule_analysis("equipment", (TODAY, [], {}))
    worker.analysis_thread.start()
    try:
        assert equipment_done.wait(1)
        worker._schedule_analysis("workshop", (TODAY,))
        assert recovered.wait(1)
    finally:
        worker.stop()
    assert not worker.analysis_thread.is_alive()


@pytest.mark.parametrize("mode, llm_enabled", [("demo", True), ("sqlserver", False)])
def test_current_disabled_modes_do_not_start_unneeded_analysis(monkeypatch, mode, llm_enabled):
    settings = SimpleNamespace(effective_data_source_mode=mode, llm_enabled=llm_enabled)
    worker = CurrentReportRefreshWorker(settings, None, None, None, None)
    if mode == "demo":
        worker.start()
        assert not worker.thread.is_alive()
        assert not worker.analysis_thread.is_alive()
    else:
        worker._schedule_analysis("workshop", (TODAY,))
        assert not worker._analysis_pending
    worker.stop()
    worker._schedule_analysis("workshop", (TODAY,))
    assert not worker._analysis_pending
