"""Pin report model inputs and source versions."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.database.metadata import MetadataRepository
from app.models.domain import (
    EquipmentRecord, LeadershipPeriodRecord, LeadershipReportResult,
    ProjectDistributionRecord, WorkshopRecord,
)
from app.services.report_analysis_service import ReportAnalysisService, generate_data_version


def analysis_cases():
    stamp = datetime(2026, 8, 18, 10, 30)
    workshop = [WorkshopRecord(
        task_id=str(index), project_code='P1' if index % 2 else None, ship_no=None,
        segment_name='S1', program_no='program.gen', cut_length=Decimal('12.345'),
        groove_length=Decimal('0.25'), work_time=Decimal(seconds),
        production_time=stamp, production_date=stamp.date(),
        confirmed_by='工人' if index % 2 else None,
        confirmed_by_company_no='001' if index % 2 else None,
        confirmed_at=stamp if index % 2 else None,
    ) for index, seconds in enumerate(('60', '90', '100', '110', '1800'))]
    equipment = [EquipmentRecord(
        task_id=row.task_id, equipment_id='Cutter01' if index % 2 else None,
        project_code=row.project_code, ship_no=row.ship_no, segment_name=row.segment_name,
        cut_length=row.cut_length, work_time=row.work_time,
        gas_consumption=Decimal('1.234') if index % 2 else None,
        electricity_consumption=Decimal('2.345') if index % 2 else None,
        production_time=stamp, production_date=stamp.date(),
        gas_data_status=('missing', 'interpolated', 'overlap', 'metered', 'invalid_interval')[index],
        electricity_data_status=('gap', 'metered', 'overlap', 'metered', 'unconfigured')[index],
    ) for index, row in enumerate(workshop)]
    total = LeadershipPeriodRecord(date(2026, 8, 1), 43, Decimal('123.456'), Decimal('2.3'), 3600)
    previous = replace(total, period_start=date(2026, 7, 1), task_count=40)
    report = LeadershipReportResult(
        report_month=date(2026, 8, 1), source_mode='demo',
        daily_records=[replace(total, period_start=date(2026, 8, day), task_count=count)
                       for day, count in enumerate((1, 3, 4, 5, 30), 1)],
        monthly_records=[previous, total], current_month_total=total, twelve_month_total=total,
        year_ago_total=replace(previous, period_start=date(2025, 8, 1)),
        project_distribution=[ProjectDistributionRecord('未标注工程', 43, Decimal('123.456'), 3600)],
    )
    comparisons = dict(comparison_records=[], comparison_date='2026-08-17',
                       year_ago_records=workshop[:2], year_ago_date='2025-08-18')
    return {
        'leadership_month': ('leadership', [report], {}),
        'leadership_range': ('leadership', [replace(report, period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 18), comparison_total=previous,
            comparison_start=date(2026, 7, 14), comparison_end=date(2026, 7, 31))], {'monthly_records': [total]}),
        'leadership_empty': ('leadership', [replace(report, daily_records=[], monthly_records=[],
            current_month_total=replace(total, task_count=0, cut_length_m=Decimal('0'), work_time_seconds=0),
            year_ago_total=None, project_distribution=[])], {}),
        'workshop': ('workshop', ['2026-08-18', workshop], comparisons),
        'workshop_empty': ('workshop', ['2026-08-18', []], {}),
        'equipment_partial': ('equipment', ['2026-08-18', equipment], {
            'energy_data_mode': 'partial', 'energy_units': {'gas': 'Nm³', 'electricity': 'kWh'},
            'energy_notice': '待核对', 'energy_summary_rows': [{'month': '2026-08', 'value': 12.345}],
            'comparison_records': equipment[:2], 'comparison_date': '2026-08-17',
            'year_ago_records': [], 'year_ago_date': '2025-08-18', 'source_data_version': 'source-v1'}),
        'equipment_mock': ('equipment', ['2026-08-18', equipment], {'energy_data_mode': 'mock'}),
        'equipment_empty': ('equipment', ['2026-08-18', []], {}),
    }


@pytest.mark.parametrize('case', analysis_cases())
def test_report_context_has_scope_and_valid_candidate_evidence(tmp_path, monkeypatch, case):
    kind, args, kwargs = analysis_cases()[case]
    service = ReportAnalysisService(Settings(project_root=tmp_path, data_source_mode='demo'), SimpleNamespace(database=None))
    captured = []
    monkeypatch.setattr(service, '_analyze', lambda **values: captured.append(values))
    getattr(service, f'analyze_{kind}')(*args, **kwargs)
    context = captured[0]['context']
    assert context['scope']['report_type'] == kind
    assert all(set(c['evidence_ids']) <= {f['id'] for f in context['facts']} for c in context['candidates'])
    assert set(context['_references']) == {c['id'] for c in context['candidates']}
    assert len(generate_data_version(context)) == 64


@pytest.mark.parametrize('case', ['workshop', 'equipment_partial'])
def test_model_request_links_outlier_tasks_to_details_and_energy_statuses(state_database, tmp_path, monkeypatch, case):
    submitted = []
    def respond(request):
        submitted.append(json.loads(request.content)['messages'])
        content = dict(headline='本期生产情况', summary='任务记录需结合确认及仪表覆盖理解。', key_metric_ids=[], findings=[], actions=[])
        return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(content)}}]})
    client = httpx.Client
    monkeypatch.setattr('app.services.llm_service.httpx.Client',
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    settings = Settings(project_root=tmp_path, device_id='Cutter01', data_source_mode='demo', llm_enabled=True,
                        llm_api_key='test-key', llm_base_url='http://llm.test')
    service = ReportAnalysisService(settings, MetadataRepository(state_database, device_id=settings.device_id))
    kind, args, kwargs = analysis_cases()[case]
    kwargs.pop('source_data_version', None)
    records = args[1]
    if kind == 'equipment':
        records[1] = replace(records[1], gas_consumption=Decimal('0'),
                             energy_details={'internal_sample': 'excluded-from-model'})
    result = getattr(service, f'analyze_{kind}')(*args, **kwargs)

    assert result.mode == 'model' and len(submitted) == 1
    payload = json.loads(submitted[0][1]['content'])
    assert {'scope', 'facts', 'candidates', 'tables', 'data_quality'} <= payload.keys()
    assert len(payload['facts']) >= 6 and len(payload['tables']) == 2
    table = payload['tables'][0]
    assert table['row_count'] == len(records)
    assert set(row['task_id'] for row in table['rows']) <= {record.task_id for record in records}
    assert table['sample_count'] <= table['row_count']
    assert '样本' in table['coverage']
    rows = {row['task_id']: row for row in table['rows']}
    longest = max(records, key=lambda record: record.work_time)
    fact_id = 'F006' if kind == 'workshop' else 'F005'
    fact = next(fact for fact in payload['facts'] if fact['id'] == fact_id)
    assert f"任务 {longest.task_id} 工时" in fact['source']
    assert rows[longest.task_id]['segment_name'] == longest.segment_name
    for record in records:
        if record.task_id not in rows:
            continue
        row = rows[record.task_id]
        assert row['project_code'] == record.project_code and row['ship_no'] == record.ship_no
        if kind == 'workshop':
            assert row['confirmed_by'] == record.confirmed_by
            assert 'confirmed_by_id' not in row
        else:
            assert row['equipment_id'] == record.equipment_id
            assert row['gas_data_status'] == record.gas_data_status
            assert row['electricity_data_status'] == record.electricity_data_status
            assert 'energy_details' not in row
    if kind == 'equipment':
        assert rows[records[0].task_id]['gas_consumption'] is None
        assert rows[records[1].task_id]['gas_consumption'] == 0
        assert 'excluded-from-model' not in submitted[0][1]['content']


def test_leadership_requires_real_comparison_dates_for_custom_ranges():
    from app.services.report_analysis.leadership import build_context
    report = analysis_cases()['leadership_month'][1][0]
    month = build_context(report, report.monthly_records)
    assert month['scope'] | {} == dict(report_type='leadership', period_start='2026-08-01', period_end='2026-08-31',
        period_mode='month', comparison_start='2026-07-01', comparison_end='2026-07-31')
    for end in [date(2026, 8, 1), date(2026, 8, 18)]:
        custom = build_context(replace(report, period_start=date(2026, 8, 1), period_end=end), report.monthly_records)
        assert custom['scope']['period_end'] == end.isoformat()
        assert 'comparison_start' not in custom['scope']
        assert custom['comparisons']['task_yoy_percent'] is None
        assert all(c['id'] != 'C_comparison' for c in custom['candidates'])
    stale = replace(report, monthly_records=[replace(report.monthly_records[0], period_start=date(2026, 6, 1))])
    assert 'comparison_start' not in build_context(stale, stale.monthly_records)['scope']


def test_workshop_links_keep_all_pending_tasks_while_model_input_is_bounded():
    from app.services.report_analysis.workshop import build_context
    from app.services.report_analysis.brief import model_context
    original = analysis_cases()['workshop'][1][1][0]
    records = [replace(original, task_id=f'task-{index}', confirmed_by=None, production_date=date(2026, 8, 18)) for index in range(250)]
    context = build_context('2026-08-18 至 2026-08-20', records)
    assert len(context['_references']['C_confirmation']) == 250
    candidate = next(c for c in context['candidates'] if c['id'] == 'C_confirmation')
    assert candidate['reference_count'] == 250 and len(candidate['reference_sample']) == 3
    table = model_context(context)['tables'][0]
    assert table['row_count'] == 250 and table['sample_count'] <= 9
    confirmed = build_context('2026-08-18', [replace(original, confirmed_by='工人')])
    assert 'C_confirmation' not in confirmed['_references']
    assert 'C_production' in confirmed['_references']
    assert build_context('2026-08-18', [])['candidates'] == []


@pytest.mark.parametrize('kind', ['workshop', 'equipment'])
@pytest.mark.parametrize('missing_top', [False, True])
def test_missing_project_groups_are_quality_candidates_with_complete_links(kind, missing_top):
    from app.services.report_analysis import workshop, equipment
    original = analysis_cases()['workshop' if kind == 'workshop' else 'equipment_partial'][1][1][0]
    records = [replace(original, task_id=f'project-{index}', ship_no=None,
                       project_code=None if (index < 3) == missing_top else 'P-real') for index in range(4)]
    context = (workshop if kind == 'workshop' else equipment).build_context('2026-08-18', records)
    fact = next(f for f in context['facts'] if f['id'] == 'F010')
    if missing_top:
        assert fact['label'] == '工程信息缺口' and '3 项' in fact['value']
        assert 'C_project' not in context['_references']
        candidate = next(c for c in context['candidates'] if c['id'] == 'C_project_missing')
        assert candidate['category'] == 'data_quality' and candidate['evidence_ids'] == ['F010']
        assert {ref['id'] for ref in context['_references']['C_project_missing']} == {'project-0', 'project-1', 'project-2'}
        assert any('不能解释为工程集中' in item for item in context['data_quality'])
        assert not any('任务最多工程' in f['label'] + f['value'] + f['source'] for f in context['facts'])
    else:
        assert fact['label'] == '任务最多工程' and 'P-real' in fact['value']
        assert 'C_project' in context['_references']


def test_leadership_missing_project_evidence_does_not_claim_project_concentration():
    from app.services.report_analysis.leadership import build_context
    report = analysis_cases()['leadership_month'][1][0]
    context = build_context(report, report.monthly_records)
    assert 'C_project_missing' in context['_references'] and 'C_project' not in context['_references']
    assert not any('任务最多工程' in f['label'] + f['value'] + f['source'] for f in context['facts'])


def test_energy_coverage_estimates_and_unavailable_are_separate_candidates():
    from app.services.report_analysis.equipment import build_context
    original = analysis_cases()['equipment_partial'][1][1][0]
    complete = replace(original, gas_consumption=Decimal('1'), electricity_consumption=Decimal('2'),
        gas_data_status='metered', electricity_data_status='metered')
    context = build_context('2026-08-18', [complete], energy_data_mode='metered')
    assert 'C_energy_quality' not in context['_references']
    assert next(f['value'] for f in context['facts'] if f['id'] == 'F011') == '100.0%'
    estimated = build_context('2026-08-18', [replace(complete, gas_data_status='estimated')], energy_data_mode='partial')
    assert len(estimated['_references']['C_energy_estimates']) == 1
    missing = build_context('2026-08-18', [original], energy_data_mode='unavailable')
    assert len(missing['_references']['C_energy_quality']) == 1
    assert next(f['value'] for f in missing['facts'] if f['id'] == 'F003') == '缺失'
    assert any('不等于零' in item for item in missing['data_quality'])


def test_long_task_candidates_do_not_lose_associations_beyond_preview_limit():
    from app.services.report_analysis.workshop import build_context
    original = analysis_cases()['workshop'][1][1][0]
    records = [replace(original, task_id=f'task-{i}', work_time=Decimal('60' if i < 100 else '3600')) for i in range(108)]
    context = build_context('2026-08-18', records)
    assert len(context['anomalies']) == len(context['_references']['C_duration']) == 8
    fact = next(f for f in context['facts'] if f['id'] == 'F006')
    assert '共 8 项' in fact['source'] and '最多展示 5 项' in fact['source']


def test_task_change_facts_explicitly_name_total_tasks():
    from app.services.report_analysis.workshop import build_context as workshop_context
    from app.services.report_analysis.equipment import build_context as equipment_context
    cases = analysis_cases()
    for kind, builder, key in [('workshop', workshop_context, 'workshop'), ('equipment', equipment_context, 'equipment_partial')]:
        records = cases[key][1][1][:2]
        context = builder('2026-08-18', records, comparison_records=[records[0]] * 11, comparison_date='2026-08-17')
        value = next(f['value'] for f in context['facts'] if f['id'] == 'F005')
        assert '任务总数比上一周期下降 81.8%' in value
        if kind == 'workshop':
            assert value.startswith('待确认 1 项；任务总数')
