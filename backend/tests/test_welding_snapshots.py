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
        if sql.startswith('SELECT COUNT(*) AS total FROM '):
            return [{'total': source.count}]
        if sql.startswith(f'SELECT {JOB_FIELDS}') or sql.startswith(f'SELECT {PART_FIELDS}'):
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
    assert count_sql.startswith('SELECT COUNT(*) AS total')
    assert count_sql.split(' WHERE ', 1)[1] == rows_sql.split(' WHERE ', 1)[1].rsplit(' ORDER BY ', 1)[0]
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
        service.refresh_summary('statistics', query().model_dump(mode='json'), requested_at=old.refreshed_at.timestamp() + 1)
    assert snapshots.get(service._snapshot_key(query())).data_version == old.data_version
    # Never disguise a failed live detail query as a successful empty table.
    with pytest.raises(WeldingUnavailable):
        service.report('jobs', query())


def test_precomputed_dashboard_queries_only_current_detail(configured):
    _, _, _, source, service = configured
    for payload, _ in service.refresh_plan():
        service.refresh_summary(payload['report_type'], payload['query'])
    before = source.calls.copy()
    today = datetime.now(ZoneInfo(service.settings.weld_db_timezone)).date()
    result = service.dashboard(WeldingQuery(start=today-timedelta(days=6), end=today))
    assert source.calls['summary'] == before['summary']
    assert source.calls['source'] == before['source']
    assert source.calls['jobs'] == before['jobs'] + 1
    assert result['today']['job_count'] == 3
    assert result['meta']['report_data']['today_data_as_of']


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
        assert current.welding.repository.calls['summary'] == len(tasks)
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


def test_periodic_worker_reuses_fresh_web_statistics_but_manual_refresh_recomputes(configured):
    _, snapshots, _, source, service = configured
    from time import time
    service.report('daily', query())
    assert source.calls['summary'] == 1
    service.refresh_summary('statistics', query().model_dump(mode='json'), requested_at=time())
    assert source.calls['summary'] == 1
    expire(service, snapshots, query())
    service.refresh_summary('statistics', query().model_dump(mode='json'), requested_at=time())
    assert source.calls['summary'] == 2
    service.report('daily', query(), force=True)
    assert source.calls['summary'] == 3
