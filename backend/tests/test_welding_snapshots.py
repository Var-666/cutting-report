from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config import Settings
from app.database.report_snapshots import ReportSnapshotRepository
from app.database.sqlite import SQLiteDatabase, execution_scope
from app.database.coordination import CoordinationRepository
from app.database.welding import WeldingUnavailable
from app.database.welding import JOB_FIELDS, PART_FIELDS, WeldingRepository
from app.models.welding import WeldingQuery
from app.services.application_runtime import build_runtime
from app.services.database_runtime import database_context
from app.services.production_reports import report_provider
from app.services.welding_service import WeldingService
from app.services.report_snapshots import SnapshotRefreshBusy, SnapshotRefreshRejected
from app.worker import WorkerProcess
from test_welding_statistics_cache import Repository


class Source(Repository):
    def __init__(self):
        super().__init__()
        self.down = False
        self.count = 3

    @contextmanager
    def snapshot(self):
        self.calls['sessions'] += 1
        if self.down:
            raise WeldingUnavailable('源库暂不可用')
        yield self

    def summary(self, session, query):
        return {**super().summary(session, query), 'source_rows': self.count, 'job_count': self.count,
                'weld_length_m': 6, 'working_hours': 2, 'involved_part_count': 2, 'flat_length_m': 6}

    def jobs(self, session, query, *, limit, total):
        self.calls['jobs'] += 1
        return [{'id': query.page, 'serial_number': 'part'}], self.count if total is None else total

    def parts(self, session, query, *, limit, total):
        self.calls['parts'] += 1
        return [{'id': query.page, 'serial_number': 'part'}], self.count if total is None else total


@pytest.mark.parametrize('kind', ['jobs', 'parts'])
def test_paging_uses_one_live_count_with_identical_predicates_and_keeps_cached_statistics(configured, kind):
    _, _, _, source, service = configured
    sql_calls = []
    original_fetch = source.fetch
    def fetch(sql, values=(), **kwargs):
        sql_calls.append((sql, values))
        if sql.startswith('SELECT COUNT(*) AS total FROM ') or '\nSELECT COUNT(*) AS total FROM ' in sql:
            return [{'total': source.count}]
        if sql.startswith(f'SELECT {JOB_FIELDS}') or f'\nSELECT {PART_FIELDS} FROM ' in sql:
            return [{'id': values[-1] + 1, 'serial_number': 'part'}]
        return original_fetch(sql, values, **kwargs)
    source.fetch = fetch
    source.jobs = lambda session, selected, **kwargs: WeldingRepository.jobs(source, session, selected, **kwargs)
    source.parts = lambda session, selected, **kwargs: WeldingRepository.parts(source, session, selected, **kwargs)
    # Real repository predicates, including zero state, are shared by COUNT and page rows.
    selected = query(device_no=0, state=0, serial_number='part', page_size=1)
    first = service.report(kind, selected)
    statistics_reads = source.calls['summary']
    source.count = 7
    sql_calls.clear()
    second = service.report(kind, selected.model_copy(update={'page': 2, 'sort_by': 'length'}))
    assert source.calls['summary'] == statistics_reads == 1
    assert second['data_version'] == first['data_version']
    assert second['total'] == 7, 'live detail totals cannot reuse an older statistics snapshot count'
    assert len(sql_calls) == 2
    count_sql, count_values = sql_calls[0]
    rows_sql, row_values = sql_calls[1]
    if kind == 'jobs':
        assert count_sql.startswith('SELECT COUNT(*) AS total')
        assert count_sql.split(' WHERE ', 1)[1] == rows_sql.split(' WHERE ', 1)[1].rsplit(' ORDER BY ', 1)[0]
    else:
        assert count_sql.split('\nSELECT', 1)[0] == rows_sql.split('\nSELECT', 1)[0]
        assert count_sql.rsplit(' WHERE ', 1)[1] == rows_sql.rsplit(' WHERE ', 1)[1].rsplit(' ORDER BY ', 1)[0]
    assert row_values[:-2] == count_values
    assert row_values[-2:] == (1, 1)
    assert ('j.state = %s' if kind == 'jobs' else 'p.state = %s') in rows_sql
    assert 'LIMIT %s OFFSET %s' in rows_sql and ', id DESC' in rows_sql
    assert second['meta']['report_data']['details_source'] == 'database'


@pytest.mark.parametrize(('legacy', 'part_state', 'expected'), [(0, None, 0), (3, None, 3), (4, 0, 0), (4, 3, 3), (None, None, None)])
def test_parts_state_response_keeps_legacy_compatibility_and_canonical_precedence(configured, legacy, part_state, expected):
    _, _, _, _, service = configured
    result = service.report('parts', query(state=legacy, part_state=part_state))
    assert result['query']['state'] is None
    assert result['query']['part_state'] == expected


def query(**changes):
    return WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 27), **changes)


@pytest.fixture
def configured(tmp_path):
    cfg = Settings(project_root=tmp_path, data_source_mode='demo', weld_enabled=True,
                   weld_db_host='weld.test', weld_db_user='reader', weld_db_password='test-only',
                   worker_database_auth_enabled=False, file_database_enabled=False, llm_enabled=False,
                   alarm_monitor_enabled=False, energy_api_enabled=False)
    database = SQLiteDatabase(cfg.metadata_db_path)
    snapshots = ReportSnapshotRepository(database)
    snapshots.initialize()
    queue = CoordinationRepository(database)
    queue.initialize()
    source = Source()
    service = WeldingService(cfg, source, snapshots=snapshots, coordination=queue)
    return cfg, snapshots, queue, source, service


def expire(service, snapshots, selected, report_type='daily'):
    key = service._snapshot_key(selected, report_type)
    old = snapshots.get(key)
    snapshots.save(replace(old, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    service.snapshot_service.revision.publish()
    return old


def test_four_reports_keep_statistics_and_analysis_versions_and_share_persistent_aggregates(configured):
    cfg, snapshots, queue, source, service = configured
    live = WeldingService(cfg, Source())
    for kind in ('daily', 'equipment', 'jobs', 'parts'):
        expected = live.report(kind, query())
        actual = service.report(kind, query())
        for field in ('summary', 'daily', 'equipment', 'device_distribution', 'projects', 'speed_distribution', 'items', 'total', 'data_version'):
            assert actual[field] == expected[field], (kind, field)
        assert actual['meta']['report_data']['line_id'] == 'welding'
        assert actual['meta']['report_data']['data_version'] == actual['data_version']
    assert source.calls['summary'] == source.calls['daily'] == source.calls['equipment'] == 1
    count = source.calls.copy()
    restarted = WeldingService(cfg, source, snapshots=snapshots, coordination=queue)
    assert restarted.report('daily', query())['meta']['report_data']['source'] == 'snapshot'
    assert restarted.report('equipment', query())['items'] == []
    assert source.calls == count, 'L2 aggregate hits must not even open MySQL'
    for kind in ('jobs', 'parts'):
        first = restarted.report(kind, query(page_size=1))
        source.count = 7
        second = restarted.report(kind, query(page=2, page_size=1, sort_by='length'))
        assert second['items'][0]['id'] == 2 and second['total'] == 7
        assert first['data_version'] == second['data_version']
    assert source.calls['summary'] == 1
    assert 'items' not in snapshots.get(service._snapshot_key(query())).payload


def test_zero_filters_report_types_and_source_changes_are_isolated(configured):
    cfg, snapshots, queue, source, service = configured
    service.report('daily', query())
    selected = query(device_no=0, robot_no=0, weld_type=0)
    service.report('jobs', selected)
    assert service._snapshot_key(selected, 'jobs') != service._snapshot_key(selected, 'parts')
    service.report('jobs', selected.model_copy(update={'page': 2}))
    assert source.calls['summary'] == 2
    service.report('jobs', query(device_no=1))
    assert source.calls['summary'] == 3
    changed = WeldingService(replace(cfg, weld_db_host='another-source'), Source(), snapshots=snapshots, coordination=queue)
    assert changed.source_identity() != service.source_identity()
    assert changed.report('daily', query())['meta']['report_data']['source'] == 'database'
    service.report('daily', query(), force=True)
    assert source.calls['summary'] == 4


def test_stale_aggregate_survives_outage_and_failed_worker_refresh(configured):
    _, snapshots, queue, source, service = configured
    first = service.report('daily', query())
    old = expire(service, snapshots, query())
    source.down = True
    stale = service.report('daily', query())
    assert stale['data_version'] == first['data_version']
    assert stale['meta']['report_data']['stale'] and stale['meta']['report_data']['refreshing']
    assert service.report('equipment', query())['meta']['report_data']['stale']
    forced = service.report('daily', query(), force=True)
    assert forced['meta']['report_data']['refresh_failed']
    with pytest.raises(WeldingUnavailable):
        service.fetch_summary('statistics', query(), force=True, requested_at=old.refreshed_at.timestamp() + 1)
    assert snapshots.get(service._snapshot_key(query())).data_version == old.data_version
    # Never disguise a failed live detail query as a successful empty table.
    with pytest.raises(WeldingUnavailable):
        service.report('jobs', query())


def test_precomputed_dashboard_opens_no_mysql_and_does_not_compute_part_statistics(configured, monkeypatch):
    cfg, snapshots, queue, source, service = configured
    report_statistics = service._report_statistics
    def statistics(session, kind, selected):
        assert kind == 'jobs', 'homepage must not wait for part statistics'
        return report_statistics(session, kind, selected)
    monkeypatch.setattr(service, '_report_statistics', statistics)
    payload, _ = service.refresh_plan()[0]
    assert payload['report_type'] == 'dashboard'
    service.refresh_summary(payload['report_type'], payload['query'])
    before = source.calls.copy()
    today = datetime.now(ZoneInfo(service.settings.weld_db_timezone)).date()
    result = service.dashboard(WeldingQuery(start=today-timedelta(days=6), end=today))
    assert source.calls == before
    assert result['today']['job_count'] == 3
    assert result['query']['page_size'] == 10
    assert result['meta']['report_data']['details_source'] == 'snapshot'
    source.down = True
    restarted = WeldingService(cfg, source, snapshots=snapshots, coordination=queue)
    assert restarted.dashboard(WeldingQuery.model_validate(payload['query']))['items'] == result['items']
    assert source.calls == before


def test_cold_dashboard_only_enqueues_one_background_refresh(configured):
    _, _, queue, source, service = configured
    for _ in range(2):
        with pytest.raises(SnapshotRefreshBusy, match='后台准备'):
            service.dashboard(query())
    assert not source.calls
    with queue.connect() as connection:
        tasks = connection.execute('SELECT * FROM background_tasks').fetchall()
    assert len(tasks) == 1
    assert 'dashboard' in tasks[0]['payload']


def test_dashboard_keeps_latest_ten_jobs_when_the_source_contains_more_than_ten(configured):
    _, _, _, source, service = configured
    source.count = 100
    queries = []
    original = source.fetch
    def fetch(sql, values=(), **kwargs):
        if sql.startswith(f'SELECT {JOB_FIELDS}'):
            queries.append((sql, values))
            assert values[-2:] == (10, 0)
            return [{'id': index, 'serial_number': 'part'} for index in range(100, 90, -1)]
        return original(sql, values, **kwargs)
    source.fetch = fetch
    source.jobs = lambda session, selected, **kwargs: WeldingRepository.jobs(source, session, selected, **kwargs)
    service.refresh_summary('dashboard', query().model_dump(mode='json'))
    report = service.dashboard(query())
    assert len(queries) == 1
    assert report['total'] == 100 and len(report['items']) == 10
    assert report['items'][0]['id'] == 100 and report['items'][-1]['id'] == 91


def test_dashboard_keeps_expired_snapshot_and_manual_refresh_is_background_only(configured):
    _, snapshots, queue, source, service = configured
    service.refresh_summary('dashboard', query().model_dump(mode='json'))
    first = service.dashboard(query())
    before = source.calls.copy()
    # Even an explicit refresh returns the existing complete page immediately.
    result = service.dashboard(query(), force=True)
    assert source.calls == before
    assert result['data_version'] == first['data_version']
    assert result['meta']['report_data']['refreshing']
    owner = queue.acquire('worker', ttl=30)
    task = queue.claim_task(owner, analysis=False)
    source.count = 9
    service.refresh_summary('dashboard', task['payload']['query'], requested_at=task['created_at'])
    queue.finish_task(task, owner)
    fresh = service.dashboard(query())
    assert fresh['summary']['job_count'] == fresh['today']['job_count'] == 9
    assert fresh['data_version'] != first['data_version']
    assert not fresh['meta']['report_data']['refreshing']
    old = expire(service, snapshots, query(), 'dashboard')
    snapshots.save(replace(old, expires_at=datetime.now(timezone.utc) - timedelta(days=2)))
    service.snapshot_service.revision.publish()
    before = source.calls.copy()
    source.down = True
    stale = service.dashboard(query())
    assert source.calls == before
    assert stale['data_version'] == fresh['data_version']
    assert stale['meta']['report_data']['stale'] and stale['meta']['report_data']['refreshing']
    with pytest.raises(WeldingUnavailable):
        service.refresh_summary('dashboard', query().model_dump(mode='json'))
    assert service.dashboard(query())['data_version'] == fresh['data_version']


@pytest.mark.parametrize('selected', [date(2026, 10, 28), date(2026, 11, 1)])
def test_refresh_plan_warms_each_default_page_with_homepage_first(configured, monkeypatch, selected):
    _, _, _, _, service = configured
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(selected.year, selected.month, selected.day, tzinfo=zone)
    monkeypatch.setattr('app.services.welding_service.datetime', Clock)
    periods = [(payload['query']['start'], payload['query']['end']) for payload, _ in service.refresh_plan()]
    assert periods == [((selected - timedelta(days=6)).isoformat(), selected.isoformat()), *[(selected.isoformat(), selected.isoformat())] * 4]
    assert [payload['report_type'] for payload, _ in service.refresh_plan()] == ['dashboard', 'page:daily', 'page:jobs', 'page:parts', 'page:equipment']


@pytest.mark.parametrize('kind', ['daily', 'jobs', 'parts', 'equipment'])
def test_every_page_reads_one_coherent_snapshot_and_refreshes_only_its_own_statistics(configured, monkeypatch, kind):
    cfg, snapshots, queue, source, service = configured
    selected = query(page=2, page_size=1, device_no=0, state=0 if kind in ('jobs', 'parts') else None)
    original = service._report_statistics
    computed = []
    def statistics(session, current_kind, current_query):
        computed.append(current_kind)
        return original(session, current_kind, current_query)
    monkeypatch.setattr(service, '_report_statistics', statistics)
    with pytest.raises(SnapshotRefreshBusy):
        service.page(kind, selected)
    assert not source.calls
    owner = queue.acquire('worker', ttl=30)
    task = queue.claim_task(owner, analysis=False)
    service.refresh_summary(task['payload']['report_type'], task['payload']['query'], requested_at=task['created_at'])
    queue.finish_task(task, owner)
    first = service.page(kind, selected)
    assert computed == [kind]
    assert source.calls['sessions'] == 1, 'statistics, total and rows must share one source transaction'
    assert first['query']['page'] == 2 and first['query']['device_no'] == 0
    if kind == 'parts':
        assert first['query']['state'] is None and first['query']['part_state'] == 0
    if kind in ('jobs', 'parts'):
        assert first['items'][0]['id'] == 2 and first['total'] == 3
    before = source.calls.copy()
    source.count = 9
    source.down = True
    restarted = WeldingService(cfg, source, snapshots=snapshots, coordination=queue)
    cached = restarted.page(kind, selected, force=True)
    assert source.calls == before
    assert cached['data_version'] == first['data_version']
    assert cached['total'] == first['total'] and cached['items'] == first['items']
    assert cached['meta']['report_data']['refreshing']
    assert cached['meta']['report_data']['details_source'] in ('snapshot', 'cache')
    task = queue.claim_task(owner, analysis=False)
    with pytest.raises(WeldingUnavailable):
        restarted.refresh_summary(task['payload']['report_type'], task['payload']['query'], requested_at=task['created_at'])
    assert snapshots.get(restarted._snapshot_key(WeldingQuery.model_validate(task['payload']['query']), f'page:{kind}'))


def test_page_keys_isolate_pagination_sort_filters_and_changed_source(configured):
    cfg, snapshots, queue, source, service = configured
    service.refresh_summary('page:jobs', query(page_size=1).model_dump(mode='json'))
    before = source.calls.copy()
    for changes in ({'page': 2}, {'page_size': 20}, {'sort_by': 'length'}, {'descending': False}, {'device_no': 0}):
        with pytest.raises(SnapshotRefreshBusy):
            service.page('jobs', query(**({'page_size': 1} | changes)))
    changed = WeldingService(replace(cfg, weld_db_host='new-source'), source, snapshots=snapshots, coordination=queue)
    with pytest.raises(SnapshotRefreshBusy):
        changed.page('jobs', query(page_size=1))
    assert source.calls == before


def test_page_queue_rejection_is_distinct_from_preparation_and_retains_available_data(configured, monkeypatch):
    _, _, queue, source, service = configured
    monkeypatch.setattr(queue, 'request_snapshot_refresh', lambda *args, **kwargs: False)
    with pytest.raises(SnapshotRefreshRejected, match='队列已满'):
        service.page('jobs', query())
    assert not source.calls
    service.refresh_summary('page:jobs', query().model_dump(mode='json'))
    before = source.calls.copy()
    report = service.page('jobs', query(), force=True)
    assert source.calls == before
    assert report['meta']['report_data']['refresh_failed']
    assert not report['meta']['report_data']['refreshing']


def test_page_refreshes_share_sampled_source_info_without_reusing_old_business_statistics(configured, monkeypatch):
    cfg, _, _, source, service = configured
    clock = [0]
    monkeypatch.setattr('app.services.welding_service.monotonic', lambda: clock[0])
    service.refresh_summary('page:daily', query().model_dump(mode='json'))
    first = service.page('daily', query())
    source.count = 9
    source.revision = '2026-10-28 10:00:00'
    service.refresh_summary('page:jobs', query().model_dump(mode='json'))
    second = service.page('jobs', query())
    assert source.calls['source'] == 1, 'global watermarks and catalog are shared across page refreshes'
    assert second['summary']['job_count'] == 9 and first['summary']['job_count'] == 3
    assert second['meta']['source_info_as_of'] == first['meta']['source_info_as_of']
    assert second['meta']['data_updated_at']['jobs'] != source.revision
    clock[0] = max(30, cfg.report_background_refresh_seconds)
    service.refresh_summary('page:equipment', query().model_dump(mode='json'))
    third = service.page('equipment', query())
    assert source.calls['source'] == 2
    assert third['meta']['data_updated_at']['jobs'] == source.revision
    # Export remains a new source read and never consumes sampled page metadata.
    service.report('daily', query(), export_limit=100)
    assert source.calls['source'] == 3


def test_exports_bypass_snapshots_and_do_not_replace_current_page_version(configured):
    _, _, _, source, service = configured
    first = service.report('jobs', query())
    before = source.calls['summary']
    for kind in ('jobs', 'parts', 'daily', 'equipment'):
        report = service.report(kind, query(), export_limit=100)
        assert 'report_data' not in report['meta']
    assert source.calls['summary'] == before + 4
    assert service.report('jobs', query())['data_version'] == first['data_version']


def test_worker_uses_registry_dedup_capacity_and_current_database_context(configured):
    cfg, _, _, _, _ = configured
    runtime = build_runtime(cfg, role='worker')
    current = database_context(runtime)
    current.welding.repository = Source()
    process = WorkerProcess(cfg, runtime)
    try:
        process._schedule()
        process._schedule()
        queue = runtime.coordination
        with queue.connect() as connection:
            tasks = connection.execute("SELECT * FROM background_tasks WHERE kind='report_snapshot_refresh'").fetchall()
        assert len(tasks) == len(current.welding.refresh_plan())
        assert {task['device_id'] for task in tasks} == {'welding'}
        owner = queue.acquire('worker', ttl=30)
        for _ in tasks:
            task = queue.claim_task(owner, analysis=False)
            assert task['kind'] == 'report_snapshot_refresh'
            with execution_scope(cfg.metadata_db_path, 'worker', owner):
                process._handle(task)
                queue.finish_task(task, owner)
        assert current.welding.repository.calls['summary'] == len(tasks) + 1, 'homepage also computes today within its own source transaction'
        assert report_provider(current, 'Cutter01').source_identity()
        # An old task is harmless after hot configuration replaces the service.
        old = tasks[0]
        import json
        task = {**dict(old), 'payload': json.loads(old['payload'])}
        updated = WeldingService(replace(cfg, weld_db_host='new-source'), Source(), snapshots=runtime.report_snapshots, coordination=queue)
        current.welding = updated
        process._handle(task)
        assert updated.repository.calls == Counter()
        with pytest.raises(ValueError, match='未登记'):
            report_provider(current, 'missing')
    finally:
        process.stop()


def test_hot_configuration_never_reuses_previous_source_snapshots(configured, monkeypatch):
    from app.api import database
    from test_database_runtime import request
    cfg, _, _, _, _ = configured
    monkeypatch.setattr(database, 'settings', cfg)
    runtime = build_runtime(cfg, role='web')
    old = database_context(runtime).welding
    old.repository = Source()
    expected = old.report('daily', query())
    body = database.WeldingConnectionRequest(enabled=True, host='new-source', database='new-db', username='reader', password='changed-test-only')
    database.save_welding_configuration(body, request(runtime))
    new = database_context(runtime).welding
    assert old.source_identity() != new.source_identity()
    new.repository = Source()
    assert new.report('daily', query())['meta']['report_data']['source'] == 'database'
    assert old.report('daily', query())['data_version'] == expected['data_version']


def test_existing_configuration_controls_current_history_and_filtered_freshness(configured):
    cfg, _, _, _, service = configured
    today = datetime.now(ZoneInfo(cfg.weld_db_timezone)).date()
    assert service._snapshot_ttl(WeldingQuery(start=today, end=today)) == max(30, cfg.report_background_refresh_seconds)
    previous = today.replace(day=1) - timedelta(days=1)
    assert service._snapshot_ttl(WeldingQuery(start=previous, end=previous)) == cfg.leadership_report_history_cache_seconds
    assert service._snapshot_ttl(WeldingQuery(start=today, end=today, device_no=0)) == 30
    for field, value in [('weld_db_user', 'another-user'), ('weld_db_password', 'rotated-test-only'), ('weld_db_timezone', 'UTC')]:
        changed = WeldingService(replace(cfg, **{field: value}), Source())
        assert changed.source_identity() != service.source_identity()


def test_obsolete_worker_statistics_do_not_recompute_but_direct_manual_refresh_still_does(configured):
    _, snapshots, _, source, service = configured
    from time import time
    service.report('daily', query())
    assert source.calls['summary'] == 1
    service.refresh_summary('statistics', query().model_dump(mode='json'), requested_at=time())
    assert source.calls['summary'] == 1
    expire(service, snapshots, query())
    service.refresh_summary('statistics', query().model_dump(mode='json'), requested_at=time())
    assert source.calls['summary'] == 1
    service.report('daily', query(), force=True)
    assert source.calls['summary'] == 2


@pytest.mark.parametrize('kind', ['statistics', 'daily', 'jobs', 'parts', 'equipment'])
def test_legacy_refresh_tasks_are_skipped_before_opening_mysql(configured, kind, caplog):
    from time import time
    _, snapshots, _, source, service = configured
    source.down = True
    with caplog.at_level('INFO', logger='app.services.welding_service'):
        service.refresh_summary(kind, query().model_dump(mode='json'), requested_at=time())
    assert not source.calls
    assert snapshots.get(service._snapshot_key(query(), kind)) is None
    assert f'skipping superseded welding statistics refresh: report_type={kind}' in caplog.text


def test_upgrade_drains_old_aggregate_tasks_before_building_the_new_homepage(configured, caplog):
    cfg, _, _, _, _ = configured
    runtime = build_runtime(cfg, role='worker')
    current = database_context(runtime)
    source = Source()
    current.welding.repository = source
    service = current.welding
    queue = runtime.coordination
    for start in (date(2026, 10, 1), date(2026, 10, 22), date(2026, 10, 28)):
        selected = WeldingQuery(start=start, end=date(2026, 10, 28))
        queue.request_snapshot_refresh('welding', service.snapshot_service.task_payload(service._snapshot_key(selected)))
    process = WorkerProcess(cfg, runtime)
    process.owner = queue.acquire('worker', ttl=30)
    try:
        process._schedule()
        with caplog.at_level('INFO'), process.scope():
            for _ in range(3):
                task = queue.claim_task(process.owner, analysis=False)
                assert task['payload']['report_type'] == 'statistics'
                process._execute(task)
                process._flush_completions()
                assert not source.calls, 'old month/week/today packs must not delay page snapshots with source queries'
            task = queue.claim_task(process.owner, analysis=False)
            assert task['payload']['report_type'] == 'dashboard'
            process._execute(task)
            process._flush_completions()
        assert source.calls['sessions'] == 1 and source.calls['summary'] == 2
        report = service.dashboard(WeldingQuery.model_validate(task['payload']['query']))
        assert report['today']['job_count'] == 3
        assert report['meta']['report_data']['source'] == 'snapshot'
        with queue.connect() as connection:
            completed = connection.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='report_snapshot_refresh' AND status='completed'").fetchone()[0]
        assert completed == 4
        assert 'report_type=dashboard outcome=completed elapsed_ms=' in caplog.text
        assert caplog.text.count('skipping superseded welding statistics refresh') == 3
    finally:
        process.stop()
