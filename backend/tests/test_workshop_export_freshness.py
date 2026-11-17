"""A new workshop export must not reuse a consumer's older production query."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.domain import AnalysisResult, QueryBundle, WorkshopRecord
from app.services.chart_service import ChartService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_generation.workshop import prepare_report
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService
from app.services.workshop_service import WorkshopService
from tests.line_fixtures import cutting_settings


@pytest.mark.parametrize('end', [date(2026, 11, 16), date(2026, 11, 17)])
def test_new_export_reads_eight_tasks_after_consumer_cached_two(tmp_path, monkeypatch, end):
    selected = date(2026, 11, 16)
    cfg = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver', report_data_cache_seconds=600)
    record = WorkshopRecord(task_id='task-1', project_code='P1', ship_no=None, segment_name='S1', program_no='A.gen',
        cut_length=Decimal('10'), groove_length=Decimal('1'), work_time=Decimal('60'), remark=None,
        production_time=datetime(2026, 11, 16, 8), production_date=selected)
    rows = [replace(record, task_id=f'task-{index}') for index in range(1, 9)]
    source = QueryBundle([], rows[:2], [], datetime(2026, 11, 16, 9))
    consumer_query, web_query = QueryService(cfg), QueryService(cfg)
    calls = []
    def fetch(filters):
        calls.append(filters)
        assert filters.start_date == selected and filters.end_date == end
        assert filters.project_code in {None, 'P1'} and filters.ship_no is None
        return source
    monkeypatch.setattr(consumer_query, '_sqlserver_workshop_data', fetch)
    monkeypatch.setattr(web_query, '_sqlserver_workshop_data', fetch)
    confirmations = SimpleNamespace(get_confirmations=lambda ids: {'task-8': dict(worker_id='worker', worker_name='工人甲',
        worker_company_no='001', confirmed_at='2026-11-16T10:00:00')})
    consumer_range = RangeReportService(cfg, consumer_query, confirmations, None)
    web_range = RangeReportService(cfg, web_query, confirmations, None)
    assert len(consumer_range.workshop_records(selected, end, project_code='P1')[1]) == 2
    source = replace(source, workshop_records=rows, data_updated_at=datetime(2026, 11, 16, 10))
    page = WorkshopService(None, confirmations, cfg, range_report_service=web_range)
    visible = page.list_range_tasks(selected, end, 'P1', 'all', 1, 10)
    assert visible['total'] == 8
    analysis = AnalysisResult(status='success', overview='保留成功分析', features='', alerts='', suggestions='')
    prepared = prepare_report(dict(report_id='fresh-export', report_type='workshop', report_date=selected.isoformat(),
        period_start=selected.isoformat(), period_end=end.isoformat(), project_code='P1', ship_no=None), 'workshop',
        settings=cfg, workshop_repository=None, range_service=consumer_range, query_service=consumer_query,
        statistics_service=StatisticsService(), chart_service=ChartService(),
        analysis_service=SimpleNamespace(cached_analysis=lambda *a, **kw: analysis),
        ensure_active=lambda: None, update_status=lambda *a: None, analyze=lambda run, *, cached: cached())
    assert prepared.task_count == visible['total']
    assert {row['task_id'] for row in prepared.report['workshop_records']} == {row['task_id'] for row in visible['items']}
    assert prepared.report['summary']['total_cut_length'] == 80
    assert prepared.report['worker_summaries'][0]['confirmed_task_count'] == 1
    assert prepared.report['meta']['data_updated_at'].startswith('2026-11-16T10:00:00')
    assert len(calls) == 3
    html = ReportService(cfg).environment.get_template('report.html.j2').render(report=prepared.report,
        echarts_source='', device_name='切割一线', demo_mode=False, energy_connected=False)
    assert all(f'task-{index}' in html for index in range(1, 9))
