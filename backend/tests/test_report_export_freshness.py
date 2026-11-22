"""New leadership and equipment reports must read current production data."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.domain import AnalysisResult, EquipmentRecord, QueryBundle, SummaryBaseRecord
from app.services.chart_service import ChartService
from app.modules.energy.periods import EnergyPeriodService
from app.modules.cutting.leadership import LeadershipReportService
from app.modules.cutting.service import EquipmentQueryResult, QueryService
from app.modules.cutting.range_reports import RangeReportService
from app.services.report_generation.equipment import prepare_report as equipment_report
from app.services.report_generation.leadership import prepare_report as leadership_report
from tests.line_fixtures import cutting_settings


DAY = date(2026, 11, 16)


def analysis_arguments():
    analysis = AnalysisResult(status='success', overview='已有成功分析', features='', alerts='', suggestions='')
    return dict(analysis_service=SimpleNamespace(cached_analysis=lambda *a, **kw: analysis),
        ensure_active=lambda: None, update_status=lambda *a: None, analyze=lambda run, *, cached: cached())


def job(kind, interval):
    return dict(report_id='fresh-export', report_type=kind, report_date=DAY.isoformat(), project_code=None, ship_no=None,
        created_at='2026-11-16T17:05:00+08:00',
        **(dict(period_start=DAY.isoformat(), period_end=DAY.isoformat()) if interval else {}))


@pytest.mark.parametrize('interval', [False, True])
def test_leadership_export_refreshes_both_period_and_month_trend(tmp_path, monkeypatch, interval):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver', report_data_cache_seconds=600,
        leadership_report_cache_seconds=600)
    query = QueryService(cfg)
    ranges = RangeReportService(cfg, query, None, None)
    leader = LeadershipReportService(cfg)
    current_count = 2
    summary = SummaryBaseRecord('task', 'P1', None, 'S1', 'A.gen', Decimal('10'), Decimal('1'), Decimal('60'),
        datetime(2026, 11, 16, 8), production_date=DAY)
    monkeypatch.setattr(query, '_sqlserver_summary_data', lambda filters: QueryBundle(
        [replace(summary, task_id=f'task-{i}') for i in range(current_count)], [], []))
    monkeypatch.setattr(query.repository, 'cutting_closing_totals', lambda start, end: [{
        'bucket_start': datetime.combine(DAY, datetime.min.time()), 'task_count': current_count, 'cut_length_m': current_count * 10,
        'groove_length_m': current_count, 'work_time_seconds': current_count * 60,
    }])
    def monthly(month):
        total = dict(task_count=current_count, cut_length_m=Decimal(current_count * 10),
            groove_length_m=Decimal(current_count), work_time_seconds=current_count * 60)
        result = leader._demo_data(month)
        record = replace(result.current_month_total, **total)
        return replace(result, current_month_total=record, monthly_records=[record], twelve_month_total=record)
    monkeypatch.setattr(leader, '_fetch_uncached', monthly)
    assert ranges.leadership(DAY, DAY).current_month_total.task_count == 2
    assert leader.fetch(DAY).current_month_total.task_count == 2
    current_count = 8
    prepared = leadership_report(job('leadership', interval), settings=cfg, leadership_service=leader,
        range_service=ranges, chart_service=ChartService(), **analysis_arguments())
    assert prepared.task_count == 8
    assert prepared.report['leadership']['current_month_total']['task_count'] == 8
    assert prepared.report['leadership']['monthly_records'][0]['task_count'] == 8
    assert prepared.report['daily_production']['totals']['task_count'] == 8
    assert prepared.report['closing_comparison'][0]['totals']['task_count'] == 0


@pytest.mark.parametrize('interval', [False, True])
def test_equipment_export_refreshes_task_rows_and_month_counts(tmp_path, monkeypatch, interval):
    cfg = cutting_settings(project_root=tmp_path, data_source_mode='sqlserver', report_data_cache_seconds=600)
    query = QueryService(cfg)
    repository = SimpleNamespace(list_month=lambda *a: [])
    energy = EnergyPeriodService(cfg, repository, query_service=query, clock=lambda: datetime(2026, 11, 17, 12))
    ranges = RangeReportService(cfg, query, None, energy)
    current_count = 2
    record = EquipmentRecord('task', cfg.energy_report_equipment_ids[0], 'P1', None, 'S1', Decimal('10'), Decimal('60'),
        None, None, production_time=datetime(2026, 11, 16, 8), production_date=DAY)
    monkeypatch.setattr(query, '_sqlserver_equipment_data', lambda filters: EquipmentQueryResult(
        [replace(record, task_id=f'task-{i}') for i in range(current_count)], datetime(2026, 11, 16, 10)))
    monkeypatch.setattr(query.repository, 'cutting_closing_totals', lambda start, end: [{
        'bucket_start': datetime.combine(DAY, datetime.min.time()), 'task_count': current_count, 'cut_length_m': current_count * 10,
        'groove_length_m': 0, 'work_time_seconds': current_count * 60,
    }])
    metric_calls = []
    def metrics(start, end):
        metric_calls.append((start, end))
        return {DAY.isoformat(): dict(task_count=current_count, work_time_seconds=current_count * 60)}
    monkeypatch.setattr(query, '_fetch_equipment_range_metrics_uncached', metrics)
    assert ranges.equipment_tasks(DAY, DAY)[1]['summary']['task_count'] == 2
    if interval:
        assert energy.get_monthly_range(date(2025, 12, 1), date(2026, 11, 30))['total_task_count'] == 2
    else:
        assert energy.get_month('2026-11')['total_task_count'] == 2
    current_count = 8
    prepared = equipment_report(job('equipment', interval), settings=cfg, query_service=query,
        range_service=ranges, energy_period_service=energy, chart_service=ChartService(), **analysis_arguments())
    assert prepared.task_count == 8
    assert len(prepared.report['equipment_records']) == 8
    assert prepared.report['energy_month']['total_task_count'] == 8
    assert prepared.report['energy_month']['total_work_time_seconds'] == 480
    assert prepared.report['equipment_summary']['total_electricity_consumption'] is None
    assert prepared.report['charts']['equipment_month']['xAxis']['data'] == [
        row['report_date'] for row in prepared.report['energy_month']['rows']
    ]
    assert prepared.report['closing_comparison'][0]['totals']['work_time_seconds'] == 480
    assert prepared.report['closing_comparison'][0]['totals']['electricity'] is None
    assert len(metric_calls) == 2
