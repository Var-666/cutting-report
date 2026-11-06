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


EXPECTED_INPUT_VERSIONS = {'leadership_month': 'ddcdfbf06c3b1994d9d78b5e1e8906c1af22fca6835f0e9bd50ba14c5d4cc7ba',
 'leadership_range': '34dd72a928e9de71e80aa9b3967562335b6ed426cadc8fff6393530d4df75d85',
 'leadership_empty': '3e8359da13c91fe6202cd7f237d59f1b83d4ec3be63ae66cb7d47d15526f8c92',
 'workshop': '2f4411fb10d90477d42c19464079072e6829b2617cab12dd50c8767d77fcd72c',
 'workshop_empty': 'a63b3f2e1af1eba217a50ac816c976cbbfb0158d2a6442c3ba5738388f5259c8',
 'equipment_partial': '1a8ce1563347f8234af64aac8e3c95de36bd9f83814a2d2bda8dfccf191dde15',
 'equipment_mock': 'fefd04dcfa83c28d0b20de1940da79e4d0e5aad58d10b14a91705f2ee2f67d2d',
 'equipment_empty': '3544a1314de3396e9f262b86dd46b0f1c58f28c554ea1e58978cb17e2f006ff3'}


@pytest.mark.parametrize('case', analysis_cases())
def test_report_model_input_and_source_versions_match_expected(tmp_path, monkeypatch, case):
    kind, args, kwargs = analysis_cases()[case]
    service = ReportAnalysisService(Settings(project_root=tmp_path, data_source_mode='demo'), SimpleNamespace(database=None))
    captured = []
    monkeypatch.setattr(service, '_analyze', lambda **values: captured.append(values))
    getattr(service, f'analyze_{kind}')(*args, **kwargs)
    assert generate_data_version(captured[0]) == EXPECTED_INPUT_VERSIONS[case]


@pytest.mark.parametrize('case', ['workshop', 'equipment_partial'])
def test_model_request_links_outlier_tasks_to_details_and_energy_statuses(state_database, tmp_path, monkeypatch, case):
    submitted = []
    def respond(request):
        submitted.append(json.loads(request.content)['messages'])
        content = {key: key for key in ('overview', 'features', 'alerts', 'suggestions')}
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
    assert set(payload) == {'report_type', 'report_date', 'report_month', 'facts', 'tables'}
    assert len(payload['facts']) == 6 and len(payload['tables']) == 2
    table = payload['tables'][0]
    assert table['row_count'] == len(records)
    assert [row['task_id'] for row in table['rows']] == [record.task_id for record in records]
    rows = {row['task_id']: row for row in table['rows']}
    longest = max(records, key=lambda record: record.work_time)
    fact_id = 'F006' if kind == 'workshop' else 'F005'
    fact = next(fact for fact in payload['facts'] if fact['id'] == fact_id)
    assert f"任务 {longest.task_id} 工时" in fact['source']
    assert rows[longest.task_id]['segment_name'] == longest.segment_name
    for record in records:
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
