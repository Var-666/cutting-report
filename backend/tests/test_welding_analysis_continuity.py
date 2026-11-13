from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_admin
from app.api.welding import router
from app.database.metadata import MetadataRepository
from app.models.domain import AnalysisResult, to_primitive
from app.models.welding import WeldingQuery
from app.services.welding_service import WeldingService
from app.services.report_analysis_service import AnalysisUnavailableError, AnalysisDataVersionMismatch
from app.services.llm_service import AnalysisOutputValidationError
from app.core.report_deadline import ReportOperationTimeout
from app.core.request_cancellation import SqlRequestCancelled
from tests.line_fixtures import welding_settings, runtime_line


QUERY = WeldingQuery(start=date(2026, 11, 12), end=date(2026, 11, 12))


def report(version='a'):
    return dict(data_version=version * 64, summary={'job_count': 3, 'weld_length_m': 20},
        daily=[{'production_date': '2026-11-12', 'job_count': 3, 'weld_length_m': 20}], equipment=[],
        meta={'calculated_at': '2026-01-01T00:00:00+08:00'})


@pytest.fixture
def analysis_service(state_database, tmp_path, monkeypatch):
    cfg = welding_settings(project_root=tmp_path, llm_enabled=True)
    def build(settings=cfg, scope='welding:source-A:scope-1'):
        return WeldingService(settings, None, analyses=MetadataRepository(state_database, device_id=settings.device_id, analysis_scope=scope))
    model = Mock(side_effect=lambda *_: AnalysisResult(status='success', overview='原有焊接分析', features='特征',
        alerts='关注', suggestions='建议', facts=[{'label': 'job_count', 'value': 3}]))
    monkeypatch.setattr('app.services.welding_service.LlmService.analyze_context', model)
    return build, model


def test_success_survives_restart_memory_expiry_and_paging_without_calling_model(analysis_service, monkeypatch):
    build, model = analysis_service
    service = build()
    first = to_primitive(service.analyze_report('daily', QUERY, report(), 'a' * 64))
    assert first['generated_at'] != report()['meta']['calculated_at']
    monkeypatch.setattr('app.services.welding_service.monotonic', lambda: 10**15)
    restarted = build()
    paged = QUERY.model_copy(update={'page': 2, 'page_size': 50, 'sort_by': 'length', 'descending': False})
    cached = restarted.analyze_report('daily', paged, report(), 'a' * 64)
    assert cached['cached'] and cached['generated_at'] == first['generated_at']
    assert cached['data_version'] == first['data_version'] and cached['facts'] == first['facts']
    assert not cached.get('cache_note')
    assert model.call_count == 1


@pytest.mark.parametrize('failure', [TimeoutError, AnalysisOutputValidationError, ReportOperationTimeout, 'disabled'])
def test_failed_refresh_keeps_original_scope_time_version_and_facts(analysis_service, failure):
    build, model = analysis_service
    first = to_primitive(build().analyze_report('jobs', QUERY, report(), 'a' * 64))
    service = build()
    if failure == 'disabled':
        service.settings = replace(service.settings, llm_enabled=False)
    else:
        model.side_effect = failure('fixture failure')
    cached = service.analyze_report('jobs', QUERY, report('b'), 'b' * 64)
    assert cached['generated_at'] == first['generated_at'] and cached['data_version'] == first['data_version']
    assert cached['facts'] == first['facts'] and cached['cache_note'] and cached['cached']
    with service.analysis_metadata.connect() as connection:
        assert connection.execute('SELECT COUNT(*) AS n FROM report_analyses').fetchone()['n'] == 1


@pytest.mark.parametrize('change', ['date', 'filter', 'kind', 'source', 'line', 'device-scope', 'timezone'])
def test_persistent_analysis_never_crosses_scope(analysis_service, change):
    build, model = analysis_service
    service = build()
    service.analyze_report('jobs', QUERY, report(), 'a' * 64)
    selected, kind = QUERY, 'jobs'
    if change == 'date': selected = QUERY.model_copy(update={'start': date(2026, 11, 11)})
    if change == 'filter': selected = QUERY.model_copy(update={'device_no': 0})
    if change == 'kind': kind = 'daily'
    if change in {'source', 'line', 'device-scope', 'timezone'}:
        service = build(scope=f'welding:{change}:other')
    model.side_effect = TimeoutError('fixture failure')
    # The same version cannot bypass source or query isolation, even for exact hits.
    with pytest.raises(AnalysisUnavailableError):
        service.analyze_report(kind, selected, report(), 'a' * 64)


def test_model_change_reuses_old_success_with_note_and_cancellation_or_version_conflict_do_not(analysis_service):
    build, model = analysis_service
    first = to_primitive(build().analyze_report('daily', QUERY, report(), 'a' * 64))
    service = build()
    service.settings = replace(service.settings, llm_model='new-model')
    model.side_effect = TimeoutError('model unavailable')
    cached = service.analyze_report('daily', QUERY, report(), 'a' * 64)
    assert cached['cache_note'] and cached['generated_at'] == first['generated_at']
    with pytest.raises(AnalysisDataVersionMismatch):
        service.analyze_report('daily', QUERY, report('b'), 'a' * 64)
    model.side_effect = SqlRequestCancelled('cancelled')
    with pytest.raises(SqlRequestCancelled):
        service.analyze_report('daily', QUERY, report('b'), 'b' * 64)


def test_parts_state_aliases_share_scope_but_other_filters_remain_distinct(analysis_service):
    build, _ = analysis_service
    service = build()
    state = QUERY.model_copy(update={'state': 3})
    service.analyze_report('parts', state, report(), 'a' * 64)
    alias = QUERY.model_copy(update={'part_state': 3})
    assert build().cached_analysis('a' * 64, kind='parts', query=alias)['cached']
    assert build().cached_analysis('a' * 64, kind='parts', query=QUERY, historical=True) is None


def test_latest_endpoint_is_read_only_and_returns_original_analysis_after_restart(analysis_service):
    build, model = analysis_service
    first = to_primitive(build().analyze_report('daily', QUERY, report(), 'a' * 64))
    restarted = build()
    restarted.page = lambda *_: pytest.fail('cache read must not query the source')
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    app.state.lines = {'welding': runtime_line(restarted, 'welding')}
    from app.device_config import load_line_catalog
    app.state.catalog = load_line_catalog(restarted.settings.production_lines_json)
    with TestClient(app, headers={'X-Production-Line': 'welding'}) as client:
        params = dict(start='2026-11-12', end='2026-11-12', report_type='daily', data_version='b' * 64)
        response = client.get('/api/v1/welding/analysis/latest', params=params)
        assert response.status_code == 200
        cached = response.json()['data']
        from app.models.report_contracts import ReportAnalysis
        ReportAnalysis.model_validate(cached)
        assert cached['cache_note'] and cached['generated_at'] == first['generated_at']
        assert client.get('/api/v1/welding/analysis/latest', params={**params, 'device_no': 0}).json()['data'] is None
        assert client.get('/api/v1/welding/analysis/latest', params={**params, 'data_version': 'invalid'}).status_code == 422
        assert model.call_count == 1
        app.dependency_overrides.pop(require_admin)
        from app.api.auth import require_user
        app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
        assert client.get('/api/v1/welding/analysis/latest', params=params).status_code == 403


def test_historical_export_metrics_do_not_mix_new_report_values():
    from app.services.analysis_display import analysis_metrics
    assert analysis_metrics({'cache_note': '历史成功分析', 'facts': []}, 'welding', report('b')) == []
