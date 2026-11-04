"""Protect deterministic report payloads and task transitions across P03."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest

from app.services.report_analysis.common import generate_data_version
from app.models.domain import EquipmentRecord, QueryBundle
from app.services.query_service import QueryService
from app.services.report_analysis_service import ReportAnalysisService
from app.services.report_generation.equipment import analyze_legacy_daily
from test_pipeline_ai_isolation import pipeline_job


SCENARIOS = [(kind, interval) for kind in ('leadership', 'workshop', 'equipment')
             for interval in (False, True)]
EXPECTED_REPORTS = {('leadership', False): 'e4dfb35aa142a4fbace7880f107548d18a2af3ced808e6f9e9d78f11631b61ce',
 ('leadership', True): 'd75421c0908bb3ee1be0c5d8887930a274809f1537cafd707513d5dca0c0b89d',
 ('workshop', False): '3fa6ea3560fa50fc57ea4291727afc7be9be063e9c9d4952402f43aa7099f5e9',
 ('workshop', True): '2268416677ab19743eeef51f4aaa9ca889d81a085fc1422c53731a0b0a95ef4d',
 ('equipment', False): '0f1dfd31cbcfc6fd69b23021efec8d738e13842cc279450457b24112f9a6a766',
 ('equipment', True): '5d854311912aaf8e4a8ef776df1dec816cb714652683ce2e27064d294eab43af'}


def business_payload(value):
    if isinstance(value, dict):
        return {key: business_payload(item) for key, item in value.items()
                if key not in {'generated_at', 'data_updated_at', 'data_as_of', 'query_duration_ms', 'system_version'}}
    if isinstance(value, list):
        return [business_payload(item) for item in value]
    return value


def generate_report_case(state_database, directory, report_type, interval):
    pipeline, job = pipeline_job(state_database, directory, report_type, interval, llm_enabled=False)
    captured, transitions = [], []
    original_render, original_status = pipeline.report_service.render, pipeline.metadata.update_status

    def render(*, report, **kwargs):
        captured.append(deepcopy(report))
        return original_render(report=report, **kwargs)

    def update_status(report_id, status, message, **kwargs):
        transitions.append((status, message, kwargs))
        return original_status(report_id, status, message, **kwargs)

    pipeline.report_service.render = render
    pipeline.metadata.update_status = update_status
    pipeline.run(job)
    completed = pipeline.metadata.get_job(job['report_id'])
    assert completed['status'] == 'completed'
    assert (pipeline.settings.reports_dir / completed['relative_path']).is_file()
    return generate_data_version(business_payload({
        'report': captured[0], 'transitions': transitions,
        'task_count': completed['task_count'], 'ai_status': completed['ai_status'],
    }))


@pytest.mark.parametrize(('report_type', 'interval'), SCENARIOS)
def test_report_payload_and_transitions_are_unchanged(state_database, tmp_path, report_type, interval):
    assert generate_report_case(state_database, tmp_path, report_type, interval) == EXPECTED_REPORTS[(report_type, interval)]


def test_legacy_daily_comparisons_reuse_raw_equipment_records_and_calendar_dates():
    record = EquipmentRecord('current', 'Cutter01', 'P1', None, None, None, None, None, None)
    previous, year_ago = [replace(record, task_id='previous')], [replace(record, task_id='year-ago')]
    query, analysis = Mock(spec=QueryService), Mock(spec=ReportAnalysisService)
    query.fetch_report_data.side_effect = [QueryBundle([], [], previous), QueryBundle([], [], year_ago)]
    result = analyze_legacy_daily(date(2024, 2, 29), [record], comparison_date=date(2024, 2, 28),
        energy_data_mode='unavailable', energy_units={}, energy_notice='',
        query_service=query, analysis_service=analysis)
    assert result is analysis.analyze_equipment.return_value
    assert [call.args[0].report_date for call in query.fetch_report_data.call_args_list] == [date(2024, 2, 28), date(2023, 2, 28)]
    analysis.analyze_equipment.assert_called_once_with('2024-02-29', [record],
        energy_data_mode='unavailable', energy_units={}, energy_notice='',
        comparison_records=previous, comparison_date='2024-02-28',
        year_ago_records=year_ago, year_ago_date='2023-02-28')
