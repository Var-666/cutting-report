from tests.runtime_factories import make_database_services
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.database.coordination import CoordinationRepository
from app.database.report_snapshots import ReportSnapshotRepository
from app.models.pdm_statistics import PdmStatisticsQuery
from app.services.pdm_statistics import PdmStatisticsService, PDM_STATISTICS_SCOPE
from app.services.production_reports import report_provider, report_providers
from app.services.report_snapshots import SnapshotRefreshBusy, SnapshotRefreshFailed
from tests.line_fixtures import configuration
from tests.postgres_fixtures import state_settings


class Source:
    def __init__(self):
        self.calls = Counter()

    def summary(self, kind, filters, query):
        self.calls['summary'] += 1
        return []

    def group_pairs(self, kind, filters):
        self.calls['groups'] += 1
        return []

    def overview(self, kind):
        self.calls['overview'] += 1
        return [{'total': 4, 'completed': 2, 'on_time': 1}]

    def options(self, kind, filters):
        self.calls['options'] += 1
        return [{'field': 'projects', 'value': 'p1', 'label': '项目一'}]


@pytest.fixture
def configured(tmp_path, postgres_state):
    cfg = state_settings(configuration(tmp_path), postgres_state)
    source = Source()
    snapshots = ReportSnapshotRepository(postgres_state[0])
    queue = CoordinationRepository(postgres_state[0])
    service = PdmStatisticsService(source, settings=cfg, snapshots=snapshots, coordination=queue, source_identity='pdm-source')
    return cfg, source, snapshots, queue, service


def finish(queue, service):
    owner = queue.acquire('worker:test-pdm', ttl=30)
    task = queue.claim_task(owner, analysis=False)
    assert task['device_id'] == PDM_STATISTICS_SCOPE
    service.refresh_summary(task['payload']['report_type'], task['payload']['query'], requested_at=task['created_at'])
    queue.finish_task(task, owner)
    queue.release('worker:test-pdm', owner)


def test_pdm_cold_queries_deduplicate_and_restart_reuses_source_isolated_snapshots(configured, monkeypatch):
    cfg, source, snapshots, queue, service = configured
    requests = []
    request_refresh = queue.request_snapshot_refresh
    def enqueue(*args, **kwargs):
        requests.append(1)
        return request_refresh(*args, **kwargs)
    monkeypatch.setattr(queue, 'request_snapshot_refresh', enqueue)
    for _ in range(3):
        with pytest.raises(SnapshotRefreshBusy):
            service.overview()
    assert not source.calls
    assert queue.task_counts() == {'pending': 1}
    assert len(requests) == 1, 'preparation polls only inspect the already queued task'
    finish(queue, service)
    first = service.overview()
    assert first.equipment.total == 4 and first.drawing.total == 4
    assert first.meta.report_data.refreshing is False
    restarted = PdmStatisticsService(source, settings=cfg, snapshots=snapshots, coordination=queue, source_identity='pdm-source')
    assert restarted.overview().meta.report_data.data_as_of == first.meta.report_data.data_as_of
    assert source.calls == {'overview': 2}
    changed = PdmStatisticsService(source, settings=cfg, snapshots=snapshots, coordination=queue, source_identity='new-source')
    with pytest.raises(SnapshotRefreshBusy):
        changed.overview()
    assert source.calls == {'overview': 2}


def test_pdm_report_multiselect_order_shares_key_but_empty_people_remain_distinct(configured):
    _, source, _, queue, service = configured
    values = dict(document_kinds=['equipment'], start_date=date(2026, 11, 1), end_date=date(2026, 11, 30),
                  equipment={'projects': ['p2', 'p1']})
    query = PdmStatisticsQuery(**values)
    with pytest.raises(SnapshotRefreshBusy):
        service.report(query)
    finish(queue, service)
    reverse = PdmStatisticsQuery(**{**values, 'equipment': {'projects': ['p1', 'p2']}})
    report = service.report(reverse)
    assert len(report.tables) == 3
    assert report.summaries.equipment.total_rate is None
    assert source.calls == {'summary': 1, 'groups': 1}
    empty_people = reverse.model_copy(update={'equipment': reverse.equipment.model_copy(update={'responsible_people': []})})
    with pytest.raises(SnapshotRefreshBusy):
        service.report(empty_people)


def test_pdm_expired_snapshot_keeps_real_timestamp_and_queues_one_refresh(configured):
    _, source, snapshots, queue, service = configured
    with pytest.raises(SnapshotRefreshBusy):
        service.overview()
    finish(queue, service)
    key = service._snapshot_key('overview', {})
    original = snapshots.get(key)
    old = datetime.now(timezone.utc) - timedelta(seconds=700)
    snapshots.save(replace(original, data_as_of=old, refreshed_at=old, expires_at=old + timedelta(seconds=600)))
    service.snapshot_service.revision.publish()
    for _ in range(3):
        result = service.overview()
        assert result.meta.report_data.stale and result.meta.report_data.refreshing
        assert result.meta.report_data.data_as_of == old.isoformat()
    assert queue.task_counts() == {'pending': 1}
    assert source.calls == {'overview': 2}


def test_shared_provider_lookup_does_not_require_a_production_line(configured):
    _, _, _, _, service = configured
    context = make_database_services(lines={}, shared_reports={PDM_STATISTICS_SCOPE: service})
    assert report_provider(context, PDM_STATISTICS_SCOPE) is service
    assert list(report_providers(context)) == [(PDM_STATISTICS_SCOPE, service)]
    plan = service.refresh_plan()
    assert [payload['report_type'] for payload, _ in plan] == ['overview', 'report']
    assert all(interval == 300 for _, interval in plan)


def test_cold_source_waits_for_retry_and_stops_on_terminal_failure(configured, monkeypatch):
    _, _, _, queue, service = configured
    with pytest.raises(SnapshotRefreshBusy):
        service.overview()
    owner = queue.acquire('worker:test-pdm', ttl=30)
    task = queue.claim_task(owner, analysis=False)
    queue.finish_task(task, owner, 'SourceUnavailable')
    for _ in range(2):
        with pytest.raises(SnapshotRefreshBusy):
            service.overview()
    assert queue.task_counts() == {'deferred': 1}
    from app.database import coordination
    current = coordination.time()
    monkeypatch.setattr(coordination, 'time', lambda: current + 31)
    assert queue.renew('worker:test-pdm', owner, ttl=120)
    queue.admit_deferred()
    retry = queue.claim_task(owner, analysis=False)
    assert retry['task_id'] == task['task_id'] and retry['attempts'] == 2
    queue.finish_task(retry, owner, 'SOURCE_UNVERIFIED')
    with pytest.raises(SnapshotRefreshFailed):
        service.overview()
    with pytest.raises(SnapshotRefreshBusy):
        service.overview(force=True)
    queue.admit_deferred()
    retry = queue.claim_task(owner, analysis=False)
    assert retry['attempts'] == 1
    service.refresh_summary(retry['payload']['report_type'], retry['payload']['query'], requested_at=retry['created_at'])
    queue.finish_task(retry, owner)
    assert service.overview().equipment.total == 4


def test_runtime_owned_pdm_uses_consumer_scope_and_rejects_old_sources(tmp_path, postgres_state):
    from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
    from app.models.database_sources import DatabaseSourceConfig
    from app.services.application_runtime import build_runtime
    from app.services.database_runtime import database_context
    from app.worker import WorkerProcess, TaskSourceUnavailable
    cfg = configuration(tmp_path)
    sources = DatabaseSourceRegistry.from_json(cfg.database_sources_json).configs()
    sources.append(DatabaseSourceConfig(id='pdm', name='PDM', engine='sqlserver', enabled=True,
        host='pdm.test', port=1433, database='pdm', username='reader', password='test-only'))
    cfg = state_settings(replace(cfg, file_database_enabled=True, file_source_key='pdm',
        database_sources_json=source_configs_json(sources)), postgres_state)
    runtime = build_runtime(cfg, role='worker')
    process = WorkerProcess(cfg, runtime)
    queue = runtime.coordination
    process.owner = queue.acquire(process.lease_name, ttl=30)
    try:
        context = database_context(runtime)
        service = report_provider(context, PDM_STATISTICS_SCOPE)
        service.repository = Source()
        with pytest.raises(SnapshotRefreshBusy):
            service.overview()
        task = queue.claim_task(process.owner, analysis=False)
        assert task['device_id'] == PDM_STATISTICS_SCOPE
        with process.scope():
            process._handle(task)
            queue.finish_task(task, process.owner)
        assert service.overview().equipment.total == 4
        assert service.repository.calls == {'overview': 2}
        with pytest.raises(TaskSourceUnavailable):
            process._handle({**task, 'payload': {**task['payload'], 'source_fingerprint': 'retired'}})
        runtime.database_runtime.publish(replace(context, shared_reports={}), {})
        process._handle(task)
        assert service.repository.calls == {'overview': 2}
    finally:
        process.stop()
