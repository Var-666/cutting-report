"""Export analysis validates current input before using historical success."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest

from app.core.report_deadline import ReportOperationTimeout
from app.core.request_cancellation import SqlRequestCancelled
from app.database.metadata import MetadataRepository
from app.models.domain import EquipmentRecord, WorkshopRecord
from app.services.leadership_report_service import LeadershipReportService
from app.services.llm_service import LlmRequestTimeout
from app.services.pipeline import ReportPipeline, ReportPipelineCancelled, ReportAnalysisPendingError
from app.services.report_analysis_service import AnalysisUnavailableError, ReportAnalysisService, AnalysisDataVersionMismatch
from tests.analysis_fakes import model_analysis
from tests.line_fixtures import cutting_settings


@pytest.fixture
def analysis_export(tmp_path):
    settings = cutting_settings(project_root=tmp_path, llm_enabled=True)
    metadata = analysis_metadata(settings.device_id)
    service = ReportAnalysisService(settings, metadata)
    model = Mock(side_effect=model_analysis)
    service.llm.analyze_context = model
    pipeline = ReportPipeline.__new__(ReportPipeline)
    pipeline.settings, pipeline._cancellation_check = settings, None
    return service, pipeline, model


def analysis_metadata(device_id):
    metadata = Mock(spec=MetadataRepository)
    metadata.database = None
    metadata.device_id, metadata.analysis_scope = device_id, 'isolated-source'
    exact, historical = {}, {}
    def save(**values):
        exact[values['cache_key']] = deepcopy(values['result'])
        historical[values['reuse_key']] = deepcopy(values['result'])
    metadata.save_analysis.side_effect = save
    metadata.get_analysis.side_effect = lambda key, **kwargs: deepcopy(exact.get(key))
    metadata.get_reusable_analysis.side_effect = lambda key: deepcopy(historical.get(key))
    metadata.get_latest_analysis.return_value = None
    return metadata


def operation(service, kind, count):
    if kind == 'leadership':
        report = LeadershipReportService(service.settings).fetch(date(2026, 8, 1))
        total = replace(report.current_month_total, task_count=count)
        report = replace(report, current_month_total=total)
        return lambda: service.analyze_leadership(report), lambda: service.cached_analysis(kind, report)
    if kind == 'workshop':
        records = [WorkshopRecord(str(i), 'P1', None, None, None, None, None, None) for i in range(count)]
        return lambda: service.analyze_workshop('2026-08-18', records), lambda: service.cached_analysis(kind, '2026-08-18')
    records = [EquipmentRecord(str(i), 'Cutter01', 'P1', None, None, None, None, None, None) for i in range(count)]
    return lambda: service.analyze_equipment('2026-08-18', records), lambda: service.cached_analysis(kind, '2026-08-18')


def export(pipeline, operation):
    analyze, cached = operation
    return pipeline._analyze_or_wait(analyze, report_id='export-test', report_type='workshop', cached=cached)


@pytest.mark.parametrize('kind', ['leadership', 'workshop', 'equipment'])
def test_export_reuses_exact_input_regenerates_changes_and_falls_back_only_after_failure(analysis_export, kind):
    service, pipeline, model = analysis_export
    first = export(pipeline, operation(service, kind, 2))
    assert not first.cached and model.call_count == 1
    same = export(pipeline, operation(service, kind, 2))
    assert same.cached and not same.cache_note and model.call_count == 1
    changed = export(pipeline, operation(service, kind, 8))
    assert not changed.cached and model.call_count == 2
    assert changed.data_version != first.data_version
    assert not changed.cache_note
    model.side_effect = LlmRequestTimeout('fixture model outage')
    failed = export(pipeline, operation(service, kind, 9))
    assert model.call_count == 3
    assert failed.cached and failed.cache_note
    assert (failed.generated_at, failed.data_version, failed.facts, failed.brief) == (
        changed.generated_at, changed.data_version, changed.facts, changed.brief)
    model.side_effect = model_analysis
    recovered = export(pipeline, operation(service, kind, 9))
    assert model.call_count == 4 and not recovered.cached and not recovered.cache_note
    assert recovered.data_version != changed.data_version


@pytest.mark.parametrize('error', [AnalysisUnavailableError, ReportOperationTimeout, OSError])
@pytest.mark.parametrize('has_history', [False, True])
def test_preparation_failure_uses_history_only_after_attempt_or_waits(analysis_export, error, has_history):
    service, pipeline, _ = analysis_export
    previous = export(pipeline, operation(service, 'workshop', 2)) if has_history else None
    events = []
    def analyze():
        events.append('analyze')
        raise error('fixture preparation failure')
    def cached():
        events.append('history')
        return previous
    if has_history:
        assert export(pipeline, (analyze, cached)) is previous
    else:
        with pytest.raises(ReportAnalysisPendingError):
            export(pipeline, (analyze, cached))
    assert events == ['analyze', 'history']


@pytest.mark.parametrize('error', [SqlRequestCancelled, ReportPipelineCancelled, AnalysisDataVersionMismatch])
def test_cancellation_never_returns_history(analysis_export, error):
    _, pipeline, _ = analysis_export
    analyze = Mock(side_effect=error('cancelled'))
    cached = Mock()
    with pytest.raises(error):
        export(pipeline, (analyze, cached))
    cached.assert_not_called()


@pytest.mark.parametrize('kind', ['daily', 'jobs', 'parts', 'equipment'])
@pytest.mark.parametrize('format', ['html', 'pdf'])
def test_welding_export_keeps_history_for_viewing_and_uses_only_matching_analysis_in_file(tmp_path, monkeypatch, kind, format):
    from app.models.domain import AnalysisResult
    from app.models.welding import WeldingQuery
    from app.services.welding_export import WeldingExport
    from app.services.welding_service import WeldingService
    from tests.line_fixtures import welding_settings

    settings = welding_settings(project_root=tmp_path, llm_enabled=True)
    metadata = analysis_metadata(settings.device_id)
    service = WeldingService(settings, None, analyses=metadata)
    query = WeldingQuery(start=date(2026, 8, 18), end=date(2026, 8, 18))
    count = 2
    version = 'a' * 64
    def current_report(*args, **kwargs):
        return dict(data_version=version, query=query.model_dump(mode='json'), total=count, items=[],
            summary=dict(job_count=count, weld_length_m=20, involved_part_count=1, working_hours=1),
            daily=[], equipment=[], meta=dict(calculated_at='2026-08-18T10:00:00', timezone='Asia/Shanghai',
                data_updated_at={}, notices=[], quality={}, unlinked_part_count=0))
    service.report = current_report
    model = Mock(side_effect=lambda *_: AnalysisResult(status='success', overview=f'本次分析{count}项',
        features='特点', alerts='关注', suggestions='建议', facts=[dict(label='分析任务数', value=count)]))
    monkeypatch.setattr('app.services.welding_service.LlmService.analyze_context', model)
    exporter = WeldingExport(settings, service, None)
    first = exporter.prepare(kind, query, format)[0].decode()
    assert '本次分析2项' in first and model.call_count == 1
    exporter.prepare(kind, query, format)
    assert model.call_count == 1
    count, version = 8, 'b' * 64
    changed = exporter.prepare(kind, query, format)[0].decode()
    assert '本次分析8项' in changed and '本次分析2项' not in changed and model.call_count == 2
    previous = service.cached_analysis(version, kind=kind, query=query)
    count, version = 9, 'c' * 64
    model.side_effect = TimeoutError('fixture model outage')
    failed = exporter.prepare(kind, query, format)[0].decode()
    assert model.call_count == 3 and '本次分析8项' not in failed
    assert '历史分析与本次导出统计不匹配' in failed
    historical = service.cached_analysis(version, kind=kind, query=query, historical=True)
    assert historical['cache_note']
    assert (historical['data_version'], historical['generated_at'], historical['facts']) == (
        previous['data_version'], previous['generated_at'], previous['facts'])
    exporter.prepare(kind, query, 'csv')
    assert model.call_count == 3


def test_welding_export_without_success_maps_failure_to_retryable_response(tmp_path):
    from fastapi import HTTPException
    from app.api.welding import source_errors
    from app.models.welding import WeldingQuery
    from app.services.welding_export import WeldingExport
    from app.services.welding_service import WeldingService
    from tests.line_fixtures import welding_settings

    settings = welding_settings(project_root=tmp_path, llm_enabled=False)
    service = WeldingService(settings, None)
    query = WeldingQuery(start=date(2026, 8, 18), end=date(2026, 8, 18))
    service.report = lambda *a, **kw: dict(data_version='a' * 64, query=query.model_dump(mode='json'),
        total=0, items=[], meta=dict(calculated_at='', timezone='Asia/Shanghai', data_updated_at={}))
    with pytest.raises(HTTPException) as error, source_errors():
        WeldingExport(settings, service, None).prepare('jobs', query, 'html')
    assert error.value.status_code == 503 and error.value.headers['Retry-After'] == '60'
