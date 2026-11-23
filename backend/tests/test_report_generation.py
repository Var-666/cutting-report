"""Protect deterministic report payloads and task transitions across P03."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest

from app.modules.reports.analysis.common import generate_data_version
from app.models.domain import EquipmentRecord, QueryBundle
from app.modules.cutting.service import QueryService
from app.modules.reports.analysis.service import ReportAnalysisService
from app.modules.reports.generation.equipment import analyze_legacy_daily
from tests.analysis_fakes import model_analysis
from test_pipeline_ai_isolation import pipeline_job


SCENARIOS = [(kind, interval) for kind in ('leadership', 'workshop', 'equipment')
             for interval in (False, True)]
EXPECTED_REPORTS = {('leadership', False): 'aebde5a1165b21a18401b4d67fe21c96ea4470b53ccdf55ccb83aa0c1e1d031d',
 ('leadership', True): '4c12649bb3291777d11e2246671a847620ded5b84550494f094a0acd3c5c3ed7',
 ('workshop', False): '9662574de5a01189400f8731d25c502d1e4fae59eebebc1b3885498f170ed6bb',
 ('workshop', True): '6d7624f744d91466378c26c1eca955bd2ae44540a72c9fb3cc1f6b1cd1a48179',
 ('equipment', False): '56d1523a0a492d856eef552a59e94bf02cbdd58f4c4cf10e03fb091b31dc6f8d',
 ('equipment', True): 'ead5f38215683bc062b492f67ab387c24f5a4d35b77db1b7bfe5be989196957a'}


def business_payload(value):
    if isinstance(value, dict):
        return {key: business_payload(item) for key, item in value.items()
                if key not in {'generated_at', 'data_updated_at', 'data_as_of', 'query_duration_ms', 'system_version'}}
    if isinstance(value, list):
        return [business_payload(item) for item in value]
    return value


def generate_report_case(state_database, directory, report_type, interval):
    pipeline, job = pipeline_job(state_database, directory, report_type, interval)
    pipeline.analysis_service.llm.analyze_context = model_analysis
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
    # The report's closure windows are anchored to the job submission time.
    pipeline.run({**job, 'created_at': '2026-09-10T10:00:00+08:00'})
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
