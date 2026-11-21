"""A new workshop export must not reuse a consumer's older production query."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import re
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.models.domain import AnalysisResult, QueryBundle, ReportFilter, SummaryBaseRecord, WorkshopRecord
from app.services.chart_service import ChartService
from app.services.query_service import QueryService
from app.services.range_report_service import RangeReportService
from app.services.report_analysis.workshop import build_context
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
    def fetch(filters, *, business_days=False):
        calls.append(business_days)
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
    assert calls == [False, False, True, True, True]
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


@pytest.mark.parametrize('explicit_period', [False, True])
def test_workshop_export_shares_17h_tasks_across_summary_kpis_comparison_and_details(tmp_path, monkeypatch, explicit_period):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver')
    zone = ZoneInfo(cfg.timezone)
    start = datetime(2026, 11, 19, 17)
    end = datetime(2026, 11, 20, 17)
    rows = [
        dict(task_id=f'cut-{index:02}', project_code='P1', program_no=f'P-{index:02}',
             cut_length=Decimal('10'), groove_length=Decimal('2'), work_time=Decimal('60'),
             production_time=completed - timedelta(minutes=30), source_updated_at=completed,
             production_date=completed.date())
        for index, completed in enumerate([
            start - timedelta(microseconds=1),
            *(start + timedelta(minutes=i) for i in range(11)),
            *(end - timedelta(minutes=i) for i in range(1, 19)),
            end,
        ])
    ]

    def fetch(sql, params):
        assert 'source.completed_at >= CAST(? AS datetime2(7))' in sql
        assert 'source.completed_at < CAST(? AS datetime2(7))' in sql
        assert params[0] == cfg.report_machine_no
        return [row for row in rows if params[1] <= row['source_updated_at'] < params[2]]

    query = QueryService(cfg, client=SimpleNamespace(fetch_all=fetch))
    assert len(query.fetch_workshop_data(ReportFilter(end.date())).workshop_records) == 19
    monkeypatch.setattr(query, 'fetch_cutting_closing_totals', lambda *args, **kwargs: {
        start.replace(tzinfo=zone): dict(task_count=28, cut_length_m=280, groove_length_m=56, work_time_seconds=1680),
    })
    confirmations = SimpleNamespace(get_confirmations=lambda ids: {})
    ranges = RangeReportService(cfg, query, confirmations, None)

    def analyze_workshop(period, records, **kwargs):
        assert kwargs['business_window'] == (start.replace(tzinfo=zone), end.replace(tzinfo=zone))
        assert {row.production_date for row in records} == {end.date()}
        assert [row.task_id for row in kwargs['comparison_records']] == ['cut-00']
        context = build_context(period, records)
        facts = {fact['id']: fact['value'] for fact in context['facts']}
        return AnalysisResult(status='success', overview='', features='', alerts='', suggestions='',
            brief=dict(headline='本期生产记录', summary=f"本期完成 {facts['F001']}，总切割长度 {facts['F002']}。",
                       findings=[], actions=[], data_quality=[], scope=context['scope']))

    job = dict(report_id='closing-workshop', report_type='workshop', report_date=end.date().isoformat(),
               project_code=None, ship_no=None, created_at=end.replace(hour=18, tzinfo=zone).isoformat())
    if explicit_period:
        job.update(period_start=job['report_date'], period_end=job['report_date'])
    prepared = prepare_report(job, 'workshop', settings=cfg, range_service=ranges, query_service=query,
        workshop_repository=SimpleNamespace(get_daily_report=lambda _: {'scope_machine_no': cfg.report_machine_no}),
        statistics_service=StatisticsService(), chart_service=ChartService(top_n=20 if explicit_period else 50),
        analysis_service=SimpleNamespace(analyze_workshop=analyze_workshop),
        ensure_active=lambda: None, update_status=lambda *_: None, analyze=lambda run, **_: run())

    assert prepared.task_count == 29
    report = prepared.report
    assert report['closing_comparison'][0]['totals'] == dict(
        task_count=29, cut_length_m=290, groove_length_m=58, work_time_seconds=1740)
    chart_programs = report['charts']['length_combo']['xAxis']['data']
    assert len(chart_programs) == (20 if explicit_period else 29)
    assert set(chart_programs) <= {row['program_no'] for row in rows[1:-1]}
    assert {row['task_id'] for row in report['workshop_records']} == {row['task_id'] for row in rows[1:-1]}
    html = ReportService(cfg).environment.get_template('report.html.j2').render(
        report=report, echarts_source='', device_name='切割一线', demo_mode=False, energy_connected=False)
    assert '本期完成 29 项，总切割长度 290.0 米。' in html
    assert ('图表 20/29 项' in html) == explicit_period
    assert re.findall(r'<strong>([^<]+)</strong>', html.split('<div class="lp-kpis">', 1)[1])[:4] == [
        '29', '290.0', '58.0', '00:29:00',
    ]
    details = html.split('车间生产明细与趋势', 1)[1].split('</table>', 1)[0]
    assert details.partition('<tbody>')[2].count('<tr>') == 29
    assert 'cut-01' in details and 'cut-29' in details
    assert 'cut-00' not in details and 'cut-30' not in details
