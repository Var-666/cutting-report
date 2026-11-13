"""Successful historical analysis remains available without publishing placeholders."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from app.core.report_deadline import ReportOperationTimeout
from app.core.request_cancellation import SqlRequestCancelled
from app.database.metadata import MetadataRepository
from app.database.postgres import execution_scope, ExecutionLeaseLost
from app.database.coordination import CoordinationRepository
from app.services.analysis_execution import analysis_operation
from app.services.report_analysis_service import ReportAnalysisService, AnalysisUnavailableError, AnalysisDataVersionMismatch
from app.services.pipeline import ReportAnalysisPendingError
from app.services.llm_service import AnalysisOutputValidationError, LlmRequestTimeout
from app.workers.report_worker import ReportWorker
from app.models.domain import WorkshopRecord
from tests.analysis_fakes import model_analysis
from tests.line_fixtures import cutting_settings


@pytest.fixture
def service(state_database, tmp_path):
    cfg = cutting_settings(project_root=tmp_path, llm_enabled=True)
    metadata = MetadataRepository(state_database, device_id='Cutter01', analysis_scope='Cutter01:source-A')
    result = ReportAnalysisService(cfg, metadata)
    result.llm.analyze_context = model_analysis
    return result


def generate(service, *, day='2026-11-12', project='A', **changes):
    records = [WorkshopRecord('task', project, None, None, None, None, None, None)]
    return service.analyze_workshop(day, records, cache_filters={'project_code': project}, force=True, **changes)


@pytest.mark.parametrize('error', [LlmRequestTimeout, AnalysisOutputValidationError, ReportOperationTimeout])
def test_failed_refresh_returns_original_success_with_original_time_and_version(service, monkeypatch, error):
    first = generate(service)
    def failed(*_):
        raise error('fixture model failure')
    monkeypatch.setattr(service.llm, 'analyze_context', failed)
    result = generate(service)
    assert result.status == 'success' and result.mode == 'model' and result.cached
    assert result.brief == first.brief and result.generated_at == first.generated_at
    assert result.data_version == first.data_version and result.facts == first.facts
    assert result.cache_note
    with service.metadata.connect() as connection:
        assert connection.execute('SELECT COUNT(*) AS n FROM report_analyses').fetchone()['n'] == 1


def test_changed_data_and_model_still_reuse_same_scope_success(service, monkeypatch):
    first = generate(service)
    service.settings = replace(service.settings, llm_model='new-model')
    monkeypatch.setattr(service.llm, 'analyze_context', lambda *_: (_ for _ in ()).throw(LlmRequestTimeout('timeout')))
    records = [WorkshopRecord(str(index), 'A', None, None, None, None, None, None) for index in range(2)]
    result = service.analyze_workshop('2026-11-12', records, cache_filters={'project_code': 'A'}, force=True)
    assert result.generated_at == first.generated_at and result.data_version == first.data_version
    assert result.facts == first.facts and result.cache_note


@pytest.mark.parametrize('change', ['date', 'filter', 'source', 'device', 'machine'])
def test_success_cache_never_crosses_request_scope(service, monkeypatch, change):
    generate(service)
    day, project = '2026-11-12', 'A'
    if change == 'date': day = '2026-11-11'
    if change == 'filter': project = 'B'
    if change in {'source', 'device'}:
        service.metadata = MetadataRepository(service.metadata.database, device_id='Cutter01' if change == 'source' else 'Cutter02',
            analysis_scope='Cutter01:source-B' if change == 'source' else 'Cutter02:source-A')
    if change == 'machine': service.settings = replace(service.settings, report_machine_no='different-machine')
    monkeypatch.setattr(service.llm, 'analyze_context', lambda *_: (_ for _ in ()).throw(LlmRequestTimeout('timeout')))
    with pytest.raises(AnalysisUnavailableError):
        generate(service, day=day, project=project)


def test_version_conflict_and_explicit_cancellation_do_not_return_cache(service, monkeypatch):
    generate(service)
    with pytest.raises(AnalysisDataVersionMismatch):
        generate(service, expected_data_version='outdated-data')
    monkeypatch.setattr(service.llm, 'analyze_context', lambda *_: (_ for _ in ()).throw(SqlRequestCancelled('cancelled')))
    with pytest.raises(SqlRequestCancelled):
        generate(service)


def test_expired_model_deadline_can_return_cache_through_outer_api_operation(service, monkeypatch):
    generate(service)
    expired = [False]
    def ensure():
        if expired[0]: raise ReportOperationTimeout('deadline')
    def failed(*_):
        expired[0] = True
        raise ReportOperationTimeout('deadline')
    monkeypatch.setattr('app.services.analysis_execution.ensure_time_remaining', ensure)
    monkeypatch.setattr(service.llm, 'analyze_context', failed)
    with analysis_operation(service.settings, 'workshop', '2026-11-12'):
        result = generate(service)
    assert result.cached and result.cache_note


def test_waiting_report_releases_consumer_and_resumes_without_emailing_placeholder(service):
    metadata = service.metadata
    metadata.create_job(report_id='waiting', device_id='Cutter01', report_type='leadership', report_date='2026-11-01',
        project_code=None, ship_no=None, request_id='waiting', created_at='2026-11-12T10:00:00+08:00')
    queue = CoordinationRepository(metadata.database)
    owner = queue.acquire('worker:continuity-test', ttl=30)
    pipeline = SimpleNamespace(run=Mock(side_effect=ReportAnalysisPendingError('waiting for model')))
    automation = Mock()
    worker = ReportWorker(service.settings, metadata, pipeline=pipeline, automation_service=automation)
    with execution_scope(metadata.database, 'worker:continuity-test', owner):
        job = metadata.claim_next_job('2026-11-12T10:00:01+08:00')
        worker._process_report(job, ZoneInfo('Asia/Shanghai'))
        waiting = metadata.get_job('waiting')
        assert waiting['status'] == 'pending' and waiting['ai_status'] == 'waiting'
        assert waiting['analysis_attempts'] == 1 and waiting['execution_owner'] is None
        assert waiting['relative_path'] is None
        automation.report_completed.assert_not_called()
        automation.report_failed.assert_not_called()
        assert metadata.claim_next_job('2026-11-12T10:00:02+08:00') is None
        with metadata.connect() as connection:
            connection.execute("UPDATE report_jobs SET analysis_next_attempt=0 WHERE report_id='waiting'")
        retry = metadata.claim_next_job('2026-11-12T10:01:02+08:00')
        assert retry['report_id'] == job['report_id']
    with metadata.connect() as connection:
        connection.execute('UPDATE process_leases SET expires_at=0 WHERE owner=%s', (owner,))
    with execution_scope(metadata.database, 'worker:continuity-test', owner), pytest.raises(ExecutionLeaseLost):
        metadata.defer_analysis('waiting', 'stale worker')


def test_waiting_status_write_retries_without_another_model_call(tmp_path):
    metadata = Mock()
    metadata.defer_analysis.side_effect = [OSError('temporary state database outage'), None]
    pipeline = SimpleNamespace(run=Mock(side_effect=ReportAnalysisPendingError('waiting for model')))
    worker = ReportWorker(cutting_settings(project_root=tmp_path, worker_poll_seconds=0), metadata, pipeline=pipeline)
    worker._process_report({'report_id': 'waiting', 'device_id': 'Cutter01'}, ZoneInfo('Asia/Shanghai'))
    assert metadata.defer_analysis.call_count == 2
    pipeline.run.assert_called_once()


@pytest.mark.parametrize('change', ['summary', 'units', 'legacy-summary'])
def test_equipment_cache_isolates_summary_window_units_and_legacy_daily_format(service, monkeypatch, change):
    rows = [dict(report_date='2026-11', task_count=0, work_time_seconds=0)]
    options = dict(energy_units={'gas': 'Nm³', 'electricity': 'kWh'}, energy_summary_rows=rows, publish_latest=True,
                   cache_filters={'summary_period_start': '2025-12-01', 'summary_period_end': '2026-11-12'})
    service.analyze_equipment('2026-11-12', [], **options)
    monkeypatch.setattr(service.llm, 'analyze_context', lambda *_: (_ for _ in ()).throw(LlmRequestTimeout('timeout')))
    assert service.analyze_equipment('2026-11-12', [], force=True, **options).cache_note
    if change == 'summary': options['cache_filters'] = {'summary_period_start': '2026-10-01', 'summary_period_end': '2026-11-12'}
    if change == 'units': options['energy_units'] = {'gas': 'm³', 'electricity': 'kWh'}
    if change == 'legacy-summary': options['cache_filters'] = {'daily_summary_month': '2026-11'}
    with pytest.raises(AnalysisUnavailableError):
        service.analyze_equipment('2026-11-12', [], force=True, **options)
