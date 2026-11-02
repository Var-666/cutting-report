"""Pin model inputs and source versions captured before the P03 split."""
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.config import Settings
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
 'workshop': 'c29743982dc4a795b1c485c4a7909965966051ede8c11b66c79b421fa4e75825',
 'workshop_empty': 'a63b3f2e1af1eba217a50ac816c976cbbfb0158d2a6442c3ba5738388f5259c8',
 'equipment_partial': 'e5721f940f01ed713f41774723503543eb6b5ad7ea34de8ac52afb20273d7401',
 'equipment_mock': 'b9d4645f1ec05b2cf2bd312010e75ef2a16bb59866cffecff1bf47564877dc18',
 'equipment_empty': '3544a1314de3396e9f262b86dd46b0f1c58f28c554ea1e58978cb17e2f006ff3'}


@pytest.mark.parametrize('case', analysis_cases())
def test_report_model_input_and_source_versions_are_unchanged(tmp_path, monkeypatch, case):
    kind, args, kwargs = analysis_cases()[case]
    service = ReportAnalysisService(Settings(project_root=tmp_path, data_source_mode='demo'), object())
    captured = []
    monkeypatch.setattr(service, '_analyze', lambda **values: captured.append(values))
    getattr(service, f'analyze_{kind}')(*args, **kwargs)
    assert generate_data_version(captured[0]) == EXPECTED_INPUT_VERSIONS[case]
