from fastapi import FastAPI
from tests.runtime_factories import install_runtime
from tests.web_fixtures import request_for
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.modules.cutting.report_reads import statistics_page
from app.runtime.application import build_runtime
from app.runtime.database import database_context
from app.modules.reports.snapshots import SnapshotRefreshBusy
from tests.line_fixtures import configuration
from tests.postgres_fixtures import state_settings


@pytest.fixture
def configured(tmp_path, postgres_state):
    runtime = build_runtime(state_settings(configuration(tmp_path), postgres_state), role='web')
    yield runtime, database_context(runtime).lines['Cutter01'].report_provider
    runtime.database_runtime.close()
    runtime.database.close()


@pytest.mark.parametrize('kind', ['leadership', 'leadership-range', 'equipment', 'workshop:source',
                                 'energy:day', 'energy:tasks', 'energy:range', 'energy:month', 'energy:monthly-range'])
def test_cutting_and_energy_pages_share_background_reads_and_persistent_results(configured, monkeypatch, kind):
    runtime, provider = configured
    today = datetime.now(ZoneInfo(provider.settings.timezone)).date()
    query = {'report_month': today} if kind in ('leadership', 'energy:month') else {'start': today, 'end': today}
    for _ in range(2):
        with pytest.raises(SnapshotRefreshBusy):
            provider.fetch_summary(kind, query)
    assert runtime.coordination.task_counts() == {'pending': 1}
    owner = runtime.coordination.acquire('worker:test-statistics', ttl=30)
    task = runtime.coordination.claim_task(owner, analysis=False)
    provider.refresh_summary(kind, task['payload']['query'], requested_at=task['created_at'])
    runtime.coordination.finish_task(task, owner)
    first = provider.page(kind, query)
    assert first['meta']['report_data']['refreshing'] is False
    assert first['meta']['report_data']['line_id'] == 'Cutter01'
    monkeypatch.setattr(provider, '_load_page', lambda *args: pytest.fail('a valid snapshot must not query the source'))
    cached = provider.page(kind, query)
    assert cached['meta']['report_data']['source'] == 'cache'
    assert cached == {**first, 'meta': {'report_data': {**first['meta']['report_data'], 'source': 'cache'}}}
    if kind == 'workshop:source':
        bundle, meta = provider.workshop_source(today, today)
        assert isinstance(bundle.workshop_records, list)
        assert meta['data_as_of'] == first['meta']['report_data']['data_as_of']
    if kind == 'leadership':
        assert date.fromisoformat(first['report_month']).day == 1


def test_cutting_http_reader_reports_cold_preparation_instead_of_zero(configured):
    from fastapi import HTTPException
    runtime, _ = configured
    app = FastAPI()
    install_runtime(app, runtime)
    request = request_for(app, request_id='cache', headers={'X-Production-Line': 'Cutter01'},
                          params={'device_id': 'Cutter01'})
    with pytest.raises(HTTPException) as error:
        statistics_page(request, 'leadership', {'report_month': date(2026, 11, 1)})
    assert error.value.status_code == 503
    assert error.value.headers['X-Report-Error'] == 'snapshot_refresh_busy'


def test_workshop_overlays_current_confirmations_without_refreshing_the_source(configured, monkeypatch):
    from app.modules.cutting.workshop import WorkshopService
    _, provider = configured
    selected = date(2026, 11, 19)
    provider.refresh_summary('workshop:source', {'report_date': selected})
    bundle, _ = provider.workshop_source(selected, selected)
    assert bundle.workshop_records
    confirmations = {}
    ranges = provider.pipeline.range_service
    monkeypatch.setattr(ranges.confirmations, 'get_confirmations', lambda ids: confirmations)
    service = WorkshopService(None, None, provider.settings, range_report_service=ranges, report_provider=provider)
    def read():
        return service.list_range_tasks(selected, selected, None, 'all', 1, 100)
    first = read()
    assert first['confirmed_count'] == 0
    task = bundle.workshop_records[0].task_id
    confirmations[str(task)] = {'worker_id': 'worker', 'worker_name': '工人', 'worker_company_no': '001',
                                'confirmed_at': '2026-11-19T10:00:00+08:00'}
    monkeypatch.setattr(provider, '_load_page', lambda *args: pytest.fail('confirmation changes do not reread SQL'))
    updated = read()
    assert updated['confirmed_count'] == 1
    assert updated['meta']['report_data']['data_as_of'] == first['meta']['report_data']['data_as_of']


def test_energy_changes_invalidate_overlapping_views_without_touching_other_scopes(configured):
    from datetime import timedelta, timezone
    from app.modules.reports.snapshot_repository import ReportSnapshot, fingerprint
    _, provider = configured
    selected = date(2026, 11, 19)
    now = datetime.now(timezone.utc)
    current = provider._snapshot_key('energy:day', provider._query('energy:day', {'report_date': selected}))
    previous = provider._snapshot_key('energy:day', provider._query('energy:day', {'report_date': date(2026, 10, 1)}))
    month = provider._snapshot_key('energy:month', provider._query('energy:month', {'report_month': selected}))
    other = replace(current, line_id='Cutter02')
    changed_source = replace(current, source_fingerprint='another-source')
    payload = {'value': 7}
    repo = provider.snapshot_service.repository
    for key in (current, previous, month, other, changed_source):
        repo.save(ReportSnapshot(key, payload, fingerprint(payload), now, now, now + timedelta(seconds=600)))
    provider.page('energy:day', {'report_date': selected})
    provider.invalidate_energy(selected, selected)
    for key in (current, month):
        result = repo.get(key)
        assert result.expires_at < result.refreshed_at
    for key in (previous, other, changed_source):
        assert repo.get(key).expires_at > now
    stale = provider.page('energy:day', {'report_date': selected})
    assert stale['meta']['report_data']['stale'] and stale['meta']['report_data']['refreshing']
    assert stale['meta']['report_data']['data_as_of'] == now.isoformat()


def test_legacy_periodic_jobs_reuse_the_same_snapshots_as_default_prewarming(configured, monkeypatch):
    from collections import Counter
    from time import time
    runtime, provider = configured
    submitted = time() - 1
    today = datetime.now(ZoneInfo(provider.settings.timezone)).date()
    calls = Counter()
    original = provider._load_page
    def load(kind, query, requested_at):
        calls[kind] += 1
        return original(kind, query, requested_at)
    monkeypatch.setattr(provider, '_load_page', load)
    for kind, query in [('leadership', {'report_month': today}), ('workshop:source', {'report_date': today}),
                        ('energy:tasks', {'report_date': today})]:
        provider.refresh_summary(kind, query, requested_at=submitted)
    current = database_context(runtime).lines['Cutter01'].cutting()
    current.leadership_refresh_worker._refresh(today.replace(day=1), False, requested_at=submitted)
    current.current_report_refresh_worker._refresh_workshop(today, requested_at=submitted)
    current.current_report_refresh_worker._refresh_equipment(today, requested_at=submitted)
    assert calls == {'leadership': 1, 'workshop:source': 1, 'energy:tasks': 1}


def test_energy_snapshot_cannot_hide_cross_day_finalization(configured, monkeypatch):
    from datetime import timedelta, timezone
    from app.modules.reports.snapshot_repository import ReportSnapshot, fingerprint
    _, provider = configured
    zone = ZoneInfo(provider.settings.timezone)
    now = datetime.now(zone)
    yesterday = now.date() - timedelta(days=1)
    completed = datetime.combine(yesterday, datetime.max.time(), tzinfo=zone)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (midnight + timedelta(minutes=1)).astimezone(tz or timezone.utc)
    monkeypatch.setattr('app.modules.reports.snapshots.datetime', Clock)
    key = provider._snapshot_key('energy:day', provider._query('energy:day', {'report_date': yesterday}))
    payload = {'is_today': True, 'value': 7}
    provider.snapshot_service.repository.save(ReportSnapshot(key, payload, fingerprint(payload), completed, completed,
        completed + timedelta(days=1)))
    monkeypatch.setattr(provider, '_load_page', lambda *args: pytest.fail('the page must retain old data and queue finalization'))
    result = provider.page('energy:day', {'report_date': yesterday})
    assert result['value'] == 7
    assert result['meta']['report_data']['stale'] and result['meta']['report_data']['refreshing']
    assert datetime.fromisoformat(result['meta']['report_data']['expires_at']) == midnight
    assert provider.snapshot_service.coordination.task_counts() == {'pending': 1}


@pytest.mark.parametrize('kind', ['energy:day', 'energy:tasks', 'energy:range', 'equipment'])
def test_energy_source_outages_do_not_replace_successful_statistics(configured, monkeypatch, kind):
    from datetime import timedelta, timezone
    from app.modules.reports.snapshot_repository import ReportSnapshot, fingerprint
    from app.modules.energy.tasks import EnergyResult
    runtime, provider = configured
    selected = date(2026, 11, 19)
    query = {'report_date': selected}
    key = provider._snapshot_key(kind, provider._query(kind, query))
    now = datetime.now(timezone.utc)
    payload = {'retained': 7}
    snapshots = provider.snapshot_service.repository
    snapshots.save(ReportSnapshot(key, payload, fingerprint(payload), now, now, now + timedelta(seconds=600)))
    energy = provider.pipeline.energy_period_service
    failure = {'warning': 'source unavailable', 'configuration_error': None, 'source_failed': True}
    monkeypatch.setattr(energy, 'configuration_error', lambda: None)
    monkeypatch.setattr(energy, 'get_day', lambda *args, **kwargs: {'sync_ok': False})
    monkeypatch.setattr(energy, 'get_task_day', lambda *args, **kwargs: failure)
    monkeypatch.setattr(provider.pipeline.range_service, 'equipment', lambda *args, **kwargs: {'task_report': failure})
    monkeypatch.setattr(provider.pipeline.energy_service, 'enrich', lambda *args, **kwargs: EnergyResult([], 'unavailable', '', {}, True))
    assert provider.page(kind, query, force=True)['retained'] == 7
    queue = runtime.coordination
    owner = queue.acquire('worker:test-outage', ttl=30)
    task = queue.claim_task(owner, analysis=False)
    with pytest.raises(ConnectionError):
        provider.refresh_summary(kind, task['payload']['query'], requested_at=task['created_at'])
    queue.finish_task(task, owner, 'ConnectionError')
    retained = provider.page(kind, query)
    assert retained['retained'] == 7
    assert retained['meta']['report_data']['refresh_failed']
    assert retained['meta']['report_data']['data_as_of'] == now.isoformat()
    assert snapshots.get(key).payload == payload
