from tests.line_fixtures import source_client
from tests.line_fixtures import as_line_services, runtime_line
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date, datetime, timedelta
import json
from threading import Event
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.analysis import AnalysisRequest, _is_default_view, router
from app.api.auth import require_admin
from app.database.metadata import MetadataRepository
from app.models.domain import ReportFilter
from app.services.leadership_report_service import LeadershipReportService
from app.services.llm_service import background_llm_work
from app.services.query_service import QueryService
from app.services.report_analysis_service import AnalysisUnavailableError, ReportAnalysisService
from tests.analysis_fakes import model_analysis
from app.services.report_analysis.workshop import build_context as build_workshop_context
from tests.test_report_analysis_service import _services


@pytest.mark.parametrize('report_type', ['leadership', 'workshop', 'equipment'])
@pytest.mark.parametrize('device_id', ['Cutter01', 'Cutter02'])
def test_page_reads_previous_success_during_refresh_then_replaces_it_without_calling_model(state_database,
    tmp_path, monkeypatch, report_type, device_id,
):
    settings, _ = _services(state_database, tmp_path)
    settings = replace(settings, device_id=device_id)
    metadata = MetadataRepository(state_database, device_id=device_id)
    service = ReportAnalysisService(settings, metadata)
    clock = [datetime(2026, 10, 21, 9, 55)]

    class Clock:
        @staticmethod
        def now(_timezone):
            return clock[0]

    monkeypatch.setattr('app.services.report_analysis_service.datetime', Clock)
    entered, release = Event(), Event()
    calls = []

    def model(kind, context):
        calls.append(context['round'])
        if context['round'] == '10:00':
            entered.set()
            assert release.wait(5)
        if context['round'] == 'failed':
            raise httpx.ReadTimeout('slow model')
        result = model_analysis(kind, context)
        result.overview = context['round']
        return result

    service.llm.analyze_context = model
    report_date = '2026-10' if report_type == 'leadership' else '2026-10-21'

    def generate(round_name, publish_latest=True):
        context = build_workshop_context('2026-10-21', [])
        context['scope']['report_type'] = report_type
        context['round'] = round_name
        with background_llm_work():
            return service._analyze(report_type=report_type, report_date=report_date,
                                    context=context, force=True,
                                    publish_latest=publish_latest)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    runtime = SimpleNamespace(report_analysis_service=service)
    app.state.lines = as_line_services({device_id: runtime})

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'latest-test'
        return await call_next(request)

    client = TestClient(app)

    def read():
        response = client.get('/api/v1/analysis/latest', params={
            'report_type': report_type, 'report_date': '2026-10-21', 'device_id': device_id,
        })
        assert response.status_code == 200
        return response.json()['data']

    assert read() is None, 'an empty cache must never start a page model call'
    assert calls == []
    generate('09:55')
    clock[0] += timedelta(minutes=5)
    with ThreadPoolExecutor(max_workers=1) as executor:
        refresh = executor.submit(generate, '10:00')
        try:
            assert entered.wait(2)
            previous = read()
            assert previous['overview'] == '09:55'
            assert previous['cached'] is True
            assert calls == ['09:55', '10:00']
        finally:
            release.set()
        newer = refresh.result(timeout=3)
    assert read()['overview'] == '10:00'
    assert read()['generated_at'] == newer.generated_at
    clock[0] += timedelta(minutes=5)
    with pytest.raises(AnalysisUnavailableError, match='超时'):
        generate('failed')
    assert read()['overview'] == '10:00', 'a failed refresh must not erase the last success'
    generate('custom summary', publish_latest=False)
    assert read()['overview'] == '10:00', 'custom queries must not replace the default snapshot'
    assert calls == ['09:55', '10:00', 'failed', 'custom summary']

    other = replace(settings, device_id='Cutter02' if device_id == 'Cutter01' else 'Cutter01')
    other_service = ReportAnalysisService(other, MetadataRepository(state_database, device_id=other.device_id))
    assert other_service.latest_default_analysis(report_type, date(2026, 10, 21)) is None
    assert service.latest_default_analysis(report_type, date(2026, 11, 21)) is None
    runtime.report_analysis_service = ReportAnalysisService(replace(settings, llm_model='another-model'), metadata)
    assert read()['overview'] == '10:00'
    assert read()['cache_note'], 'old model results remain available with an explicit historical-cache label'


@pytest.mark.parametrize('payload, expected', [
    ({'report_type': 'workshop'}, True),
    ({'report_type': 'workshop', 'period_start': '2026-10-21', 'period_end': '2026-10-21'}, True),
    ({'report_type': 'workshop', 'period_start': '2026-10-20', 'period_end': '2026-10-21'}, False),
    ({'report_type': 'equipment', 'summary_period_start': '2025-11-01', 'summary_period_end': '2026-10-21'}, True),
    ({'report_type': 'equipment', 'summary_period_start': '2026-10-01', 'summary_period_end': '2026-10-21'}, False),
    ({'report_type': 'leadership', 'summary_period_start': '2025-11-01', 'summary_period_end': '2026-10-21'}, True),
    ({'report_type': 'leadership', 'period_start': '2026-10-01', 'period_end': '2026-10-21'}, False),
])
def test_only_default_report_scope_is_published_for_other_page_visits(payload, expected):
    assert _is_default_view(AnalysisRequest(report_date='2026-10-21', **payload)) is expected


@pytest.mark.parametrize('report_type', ['leadership', 'workshop', 'equipment'])
def test_report_services_publish_the_completed_default_result(state_database, tmp_path, report_type):
    settings, service = _services(state_database, tmp_path)
    day = date(2026, 10, 21)
    if report_type == 'leadership':
        result = service.analyze_leadership(LeadershipReportService(settings, client=source_client(settings)).fetch(day.replace(day=1)), publish_latest=True)
    else:
        bundle = QueryService(settings, client=source_client(settings)).fetch_report_data(ReportFilter(report_date=day))
        result = getattr(service, f'analyze_{report_type}')(
            day.isoformat(), getattr(bundle, f'{report_type}_records'), publish_latest=True,
        )
    published = service.latest_default_analysis(report_type, day)
    assert published.generated_at == result.generated_at
    assert published.overview == result.overview
    assert published.cached
