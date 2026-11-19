"""Anomalous task facts must reach model findings and every archived rendition."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from app.models.domain import WorkshopRecord, EquipmentRecord, SummaryBaseRecord, LeadershipPeriodRecord, LeadershipReportResult
from app.services.report_analysis import workshop, equipment, leadership
from app.services.report_analysis.brief import model_context
from app.services.report_analysis.common import normalize_analysis_context
from app.services.report_analysis.brief_validation import validate_brief, validate_welding_quantities
from app.services.report_analysis_service import ReportAnalysisService
from app.services.statistics_service import StatisticsService
from app.services.report_service import ReportService
from app.services.llm_service import LlmService
from tests.line_fixtures import cutting_settings

DAY = date(2026, 11, 18)


def records():
    return [WorkshopRecord(str(identifier), 'P1', None, 'S1', f'{identifier}.gen', Decimal(cut), Decimal('0'), Decimal(work),
        production_date=DAY) for identifier, cut, work in [(31519, '1', '22'), (31490, '0', '51'), (31520, '172.9', '2440')]]


def output():
    return dict(headline='任务数据需要核查', summary='生产记录已汇总，长度与用时存在待核查记录。', key_metric_ids=[],
        findings=[dict(candidate_id='C_task_quality', title='核查长度与用时', detail='{{F020}}。建议核对原始计数、起止时间和试切记录。')],
        actions=[])


def contexts(kind):
    rows = records()
    if kind == 'workshop':
        return normalize_analysis_context(workshop.build_context(DAY.isoformat(), rows))[0]
    if kind == 'equipment':
        rows = [EquipmentRecord(row.task_id, 'Cutter01', row.project_code, row.ship_no, row.segment_name,
            row.cut_length, row.work_time, None, None, production_date=DAY) for row in rows]
        return normalize_analysis_context(equipment.build_context(DAY.isoformat(), rows))[0]
    total = LeadershipPeriodRecord(DAY, 3, Decimal('173.9'), Decimal('0'), 2513)
    report = LeadershipReportResult(DAY.replace(day=1), 'demo', [total], [total], total, total)
    summary = [SummaryBaseRecord(row.task_id, row.project_code, row.ship_no, row.segment_name, row.program_no,
        row.cut_length, row.groove_length, row.work_time, production_date=DAY) for row in rows]
    return normalize_analysis_context(leadership.build_context(report, report.monthly_records, summary))[0]


@pytest.mark.parametrize('kind', ['leadership', 'workshop', 'equipment'])
def test_task_anomalies_are_required_and_preserve_complete_records(kind):
    context = contexts(kind)
    candidate = next(item for item in context['candidates'] if item['id'] == 'C_task_quality')
    assert candidate['required'] and candidate['reference_count'] == 2
    assert {item['id'] for item in context['_references']['C_task_quality']} == {'31519', '31490'}
    fact = next(item for item in context['facts'] if item['id'] == 'F020')
    assert '共 2 项' in fact['value'] and '为零 1 项' in fact['value'] and '不足一分钟' in fact['value']
    model = model_context(context)
    assert next(table for table in model['tables'] if table['name'] == '长度与用时待核查任务')['row_count'] == 2
    result = validate_brief(output(), context)
    assert fact['value'] in result['findings'][0]['detail']
    with pytest.raises(ValueError, match='必须分析'):
        validate_brief(output() | {'findings': []}, context)
    incomplete = output()
    incomplete['findings'][0]['detail'] = '任务需要核查。'
    with pytest.raises(ValueError, match='完整覆盖'):
        validate_brief(incomplete, context)


def test_missing_anomaly_analysis_triggers_one_model_correction(monkeypatch, tmp_path):
    calls = []
    def request(*args, **kwargs):
        calls.append(json.loads(json.dumps(kwargs['payload'])))
        body = output() | {'findings': []} if len(calls) == 1 else output()
        return {'choices': [{'message': {'content': json.dumps(body, ensure_ascii=False)}}]}
    monkeypatch.setattr('app.services.llm_service.request_llm_json', request)
    service = LlmService(cutting_settings(project_root=tmp_path, llm_enabled=True, llm_api_key='test-key'), None)
    result = service.analyze_context('workshop', contexts('workshop'))
    assert len(calls) == 2 and '必须分析' in calls[1]['messages'][-1]['content']
    assert result.brief['findings'][0]['candidate_id'] == 'C_task_quality'


def test_zero_groove_alone_and_short_tasks_do_not_change_production_totals():
    rows = records()
    result = StatisticsService().calculate(rows)
    assert result.summary.task_count == 3 and result.summary.total_cut_length == Decimal('173.9')
    assert {item.task_id for item in result.data_quality} == {'31519', '31490'}
    normal = replace(rows[-1], work_time=Decimal('60'))
    assert all(item['id'] != 'C_task_quality' for item in workshop.build_context(DAY.isoformat(), [normal])['candidates'])
    groove_only = replace(normal, cut_length=Decimal('0'), groove_length=Decimal('10'))
    assert not StatisticsService().calculate([groove_only]).data_quality


def test_leadership_analysis_reads_its_own_task_period(tmp_path, monkeypatch):
    calls, captured = [], []
    rows = records()
    def fetch(filters, *, force):
        calls.append(filters)
        return SimpleNamespace(summary_records=rows)
    service = ReportAnalysisService(cutting_settings(project_root=tmp_path), SimpleNamespace(database=None),
        query_service=SimpleNamespace(fetch_summary_data=fetch))
    monkeypatch.setattr(service, '_analyze', lambda **kwargs: captured.append(kwargs))
    total = LeadershipPeriodRecord(DAY, 3, Decimal('173.9'), Decimal('0'), 2513)
    report = LeadershipReportResult(DAY.replace(day=1), 'demo', [total], [total], total, total,
        period_start=DAY, period_end=DAY)
    service.analyze_leadership(report)
    assert calls[0].start_date == calls[0].end_date == DAY
    assert any(item['id'] == 'C_task_quality' for item in captured[0]['context']['candidates'])


def test_independent_findings_cannot_repeat_the_same_text():
    context = workshop.build_context(DAY.isoformat(), [records()[-1]])
    draft = output() | {'findings': [
        dict(candidate_id='C_production', title='生产记录', detail='核对生产记录。'),
        dict(candidate_id='C_confirmation', title='生产记录', detail='核对生产记录。')]}
    with pytest.raises(ValueError, match='关键发现重复'):
        validate_brief(draft, context)


@pytest.mark.parametrize(('key', 'term'), [
    ('invalid_length_rows', '长度'), ('invalid_duration_rows', '用时'),
    ('invalid_serial_rows', '流水号'), ('invalid_time_rows', '时间'),
    ('invalid_part_rows', '工件'), ('unverified_state_rows', '状态'),
])
def test_welding_findings_must_cover_each_known_quality_issue(key, term):
    context = {'facts': [], 'tables': [{'name': '统计口径和数据质量', 'rows': [{'quality': {key: 2}}]}]}
    content = dict(overview='生产汇总', features='生产分布', alerts='暂无问题', suggestions='核对记录')
    with pytest.raises(ValueError, match='遗漏'):
        validate_welding_quantities(content, context)
    validate_welding_quantities(content | {'alerts': f'{term}存在待核查记录，请核对源数据。'}, context)
    context['tables'][0]['rows'][0]['quality'][key] = 0
    validate_welding_quantities(content, context)


@pytest.mark.parametrize('kind', ['leadership', 'workshop', 'equipment'])
def test_archived_findings_keep_anomalies_without_repeating_evidence_values(tmp_path, kind):
    context = contexts(kind)
    brief = validate_brief(output(), context)
    service = ReportService(cutting_settings(project_root=tmp_path))
    template = service.environment.from_string("""{% from 'report_analysis.html.j2' import render_analysis %}
        {{ render_analysis(analysis, report, '2026-11-18', '2026-11-18') }}""")
    fact = next(item for item in context['facts'] if item['id'] == 'F020')
    html = template.render(analysis=dict(brief=brief, facts=[fact], mode='model', status='success', cached=False), report={})
    assert html.count(fact['value']) == 1
    if kind != 'equipment':
        assert '31519.gen' in html and '31490.gen' in html
    assert brief['findings'][0]['reference_count'] == 2
    assert '数据证据 / 分析依据' not in html
    assert '待核查' in html
