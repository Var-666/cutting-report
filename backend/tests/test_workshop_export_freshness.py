"""A new workshop export must not reuse a consumer's older production query."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.models.domain import AnalysisResult, QueryBundle, SummaryBaseRecord, WorkshopRecord
from app.services.chart_service import ChartService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_generation.workshop import prepare_report
from app.services.report_service import ReportService
from app.services.statistics_service import StatisticsService
from app.services.workshop_service import WorkshopService
from tests.line_fixtures import cutting_settings


@pytest.mark.parametrize('end', [date(2026, 11, 16), date(2026, 11, 17)])
@pytest.mark.parametrize('filtered', [False, True])
def test_new_export_reads_fresh_tasks_in_the_applied_scope(tmp_path, monkeypatch, end, filtered):
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
        assert filters.project_code in {None, 'P1'} and filters.ship_no is None
        if filters.end_date < selected:
            return replace(source, workshop_records=[
                replace(record, task_id='task-8', project_code='p1'),
                replace(record, task_id='task-7'),
            ])
        assert filters.start_date == selected and filters.end_date == end
        return source
    monkeypatch.setattr(consumer_query, '_sqlserver_workshop_data', fetch)
    monkeypatch.setattr(web_query, '_sqlserver_workshop_data', fetch)
    comparison_calls = []
    def summary(filters):
        comparison_calls.append(filters)
        assert filters.start_date < selected and filters.end_date == end
        assert filters.project_code == 'P1' and filters.ship_no is None
        return QueryBundle([SummaryBaseRecord(
            row.task_id, row.project_code, row.ship_no, row.segment_name, row.program_no,
            row.cut_length, row.groove_length, row.work_time, row.production_time,
            row.production_time + timedelta(seconds=int(row.work_time)),
        ) for row in source.workshop_records], [], [])
    monkeypatch.setattr(consumer_query, '_sqlserver_summary_data', summary)
    confirmations = SimpleNamespace(get_confirmations=lambda ids: {'task-8': dict(worker_id='worker', worker_name='工人甲',
        worker_company_no='001', confirmed_at='2026-11-16T10:00:00')})
    consumer_range = RangeReportService(cfg, consumer_query, confirmations, None)
    web_range = RangeReportService(cfg, web_query, confirmations, None)
    assert len(consumer_range.workshop_records(selected, end, project_code='P1')[1]) == 2
    # SQL Server may match this casing under its collation, while the page uses the exact project key.
    source = replace(source, workshop_records=rows + [replace(record, task_id='other-project', project_code='p1')],
                     data_updated_at=datetime(2026, 11, 16, 10))
    page = WorkshopService(None, confirmations, cfg, range_report_service=web_range)
    task_id = 'task-8' if filtered else None
    confirmation_status = 'confirmed' if filtered else 'all'
    visible = page.list_range_tasks(selected, end, 'P1', confirmation_status, 1, 10, task_id=task_id)
    assert visible['total'] == (1 if filtered else 8)
    assert visible['report']['project_count'] == 1
    if filtered:
        unfiltered = page.list_range_tasks(selected, end, 'P1', 'all', 1, 10)
        assert visible['analysis_source_version'] != unfiltered['analysis_source_version']
    analysis = AnalysisResult(status='success', overview='保留成功分析', features='', alerts='', suggestions='')
    cache_requests = []
    def cached(*args, **kwargs):
        cache_requests.append(kwargs['cache_filters'])
        return analysis
    def analyze_workshop(period, records, **kwargs):
        assert period == (selected.isoformat() if selected == end else f'{selected} 至 {end}')
        assert {row.task_id for row in records} == {row['task_id'] for row in visible['items']}
        expected = [] if filtered else ['task-7']
        assert [row.task_id for row in kwargs['comparison_records']] == expected
        assert [row.task_id for row in kwargs['year_ago_records']] == expected
        return analysis
    def execute(run, *, cached):
        assert cached() is analysis
        return run()
    prepared = prepare_report(dict(report_id='fresh-export', report_type='workshop', report_date=selected.isoformat(),
        period_start=selected.isoformat(), period_end=end.isoformat(), project_code='P1', ship_no=None,
        parameters_json=json.dumps({'task_id': task_id, 'confirmation_status': confirmation_status})), 'workshop',
        settings=cfg, workshop_repository=None, range_service=consumer_range, query_service=consumer_query,
        statistics_service=StatisticsService(), chart_service=ChartService(),
        analysis_service=SimpleNamespace(cached_analysis=cached, analyze_workshop=analyze_workshop),
        ensure_active=lambda: None, update_status=lambda *a: None, analyze=execute)
    assert prepared.task_count == visible['total']
    assert {row['task_id'] for row in prepared.report['workshop_records']} == {row['task_id'] for row in visible['items']}
    assert prepared.report['summary']['total_cut_length'] == (10 if filtered else 80)
    assert prepared.report['worker_summaries'][0]['confirmed_task_count'] == 1
    assert prepared.report['meta']['data_updated_at'].startswith('2026-11-16T10:00:00')
    assert len(calls) == 5
    assert cache_requests == [{'project_code': 'P1', 'ship_no': None, **(
        {'task_id': 'task-8', 'confirmation_status': 'confirmed'} if filtered else {})}]
    assert len(comparison_calls) == (0 if filtered else 1)
    if filtered:
        assert not prepared.report['closing_comparison']
    else:
        assert prepared.report['closing_comparison'][-1]['totals']['task_count'] == 8
    html = ReportService(cfg).environment.get_template('report.html.j2').render(report=prepared.report,
        echarts_source='', device_name='切割一线', demo_mode=False, energy_connected=False)
    assert 'task-8' in html
    assert 'other-project' not in html
    assert all((f'task-{index}' in html) == (not filtered) for index in range(1, 8))
