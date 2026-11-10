import json
from dataclasses import replace

import pytest

from app.config import Settings
from app.services.llm_service import AnalysisOutputValidationError, LlmService
from app.services.llm_prompts import analysis_system_prompt
from app.services.report_analysis.workshop import build_context


def _model_response(monkeypatch, raw_content):
    requests = []
    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {'choices': [{'message': {'content': raw_content}}]}
    class FakeClient:
        def __init__(self, **_kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def post(self, _url, **kwargs):
            requests.append(kwargs['json'])
            return FakeResponse()
    monkeypatch.setattr('app.services.llm_service.httpx.Client', FakeClient)
    return requests


def _service(state_database, tmp_path):
    return LlmService(Settings(project_root=tmp_path, llm_enabled=True, llm_api_key='test-key',
                              llm_base_url='http://llm.test'), database=state_database)


def draft(**changes):
    return dict(headline='当前周期无任务记录', summary='无有效数据，无法评价生产表现。',
                key_metric_ids=[], findings=[], actions=[], **changes)


def test_cutting_empty_brief_and_empty_actions_are_valid(state_database, tmp_path, monkeypatch):
    requests = _model_response(monkeypatch, json.dumps(draft()))
    result = _service(state_database, tmp_path).analyze_context('workshop', build_context('2026-08-18', []))
    assert result.brief['headline'] == '当前周期无任务记录'
    assert result.brief['findings'] == result.brief['actions'] == []
    assert result.overview == result.brief['summary']
    assert len(requests) == 1
    submitted = json.loads(requests[0]['messages'][1]['content'])
    assert {'scope', 'facts', 'candidates', 'tables', 'data_quality'} <= submitted.keys()
    assert '_references' not in submitted


@pytest.mark.parametrize(('raw', 'fragment'), [
    ('not json', 'JSON'),
    (json.dumps({'overview': '旧结构'}), '校验失败'),
    (json.dumps(draft() | {'headline': ' '}), '为空'),
    (json.dumps(draft() | {'key_metric_ids': ['F999']}), '指标引用'),
    (json.dumps(draft() | {'findings': [{'candidate_id': 'C_fake', 'title': '发现', 'detail': '解释', 'evidence_ids': ['F001']}]}), '候选引用'),
    (json.dumps(draft() | {'actions': [{'candidate_id': 'C_fake', 'title': '核对', 'detail': '核对记录'}]}), '关联真实发现'),
    (json.dumps(draft() | {'summary': '本期有999999项任务。'}), '数量'),
    (json.dumps(draft() | {'summary': '2026-18-08记录完整。'}), '日期'),
    (json.dumps(draft() | {'findings': '发现'}), '校验失败'),
])
def test_cutting_invalid_output_is_rejected_without_repair(state_database, tmp_path, monkeypatch, raw, fragment):
    requests = _model_response(monkeypatch, raw)
    with pytest.raises(AnalysisOutputValidationError, match=fragment):
        _service(state_database, tmp_path).analyze_context('workshop', build_context('2026-08-18', []))
    assert len(requests) == 1


def test_finding_evidence_and_business_links_are_bound_by_server():
    from app.models.domain import WorkshopRecord
    from app.services.report_analysis.brief_validation import validate_brief
    record = WorkshopRecord('real-task', 'P1', None, None, 'program', None, None, None)
    context = build_context('2026-08-18', [record])
    output = dict(headline='确认归属待补齐', summary='已有切割记录，人员归属尚未登记。', key_metric_ids=['F004'],
        findings=[dict(candidate_id='C_confirmation', title='待登记确认', detail='请核对完成记录及工时归属。', evidence_ids=['F004', 'F005'])],
        actions=[dict(candidate_id='C_confirmation', title='核对完成人', detail='核对关联任务的实际完成人后登记确认。')])
    result = validate_brief(output, context)
    assert result['findings'][0]['references'][0]['id'] == 'real-task'
    output['findings'][0]['evidence_ids'] = ['F001']
    with pytest.raises(ValueError, match='事实引用'):
        validate_brief(output, context)
    output['findings'][0]['evidence_ids'] = ['F999']
    with pytest.raises(ValueError, match='事实引用'):
        validate_brief(output, context)
    output['findings'][0]['task_id'] = 'invented'
    with pytest.raises(ValueError):
        validate_brief(output, context)


def test_welding_keeps_four_fields_and_original_model_input(state_database, tmp_path, monkeypatch):
    content = {key: '焊接分析' for key in ('overview', 'features', 'alerts', 'suggestions')}
    requests = _model_response(monkeypatch, json.dumps(content))
    result = _service(state_database, tmp_path).analyze_context('welding', {'facts': [], 'tables': [], 'raw_records': ['excluded']})
    assert result.brief is None
    assert result.overview == '焊接分析'
    assert 'raw_records' not in json.loads(requests[0]['messages'][1]['content'])
    _model_response(monkeypatch, json.dumps({'overview': ''}))
    with pytest.raises(AnalysisOutputValidationError, match='四段内容'):
        _service(state_database, tmp_path).analyze_context('welding', {'facts': []})


def test_cutting_prompts_choose_dynamic_findings_and_respect_business_boundaries():
    for kind in ('leadership', 'workshop', 'equipment'):
        prompt = analysis_system_prompt(kind)
        assert all(field in prompt for field in ('headline', 'summary', 'candidate_id', 'evidence_ids', 'key_metric_ids'))
        assert '四个非空' not in prompt
        assert '待核实原因不能写成事实' in prompt
    assert '等长周期' in analysis_system_prompt('leadership')
    assert '不表示切割未完成' in analysis_system_prompt('workshop')
    assert '不等于模拟数据' in analysis_system_prompt('equipment')


def test_brief_rejects_cross_period_cross_line_links_and_reversed_changes():
    from app.models.domain import WorkshopRecord
    from app.services.report_analysis.brief_validation import validate_brief
    row = WorkshopRecord('real-task', 'P1', None, None, None, None, None, None)
    context = build_context('2026-08-18', [row])
    output = dict(headline='人员归属待补齐', summary='需核对已有切割记录。', key_metric_ids=[],
        findings=[dict(candidate_id='C_confirmation', title='待确认', detail='核对任务确认记录。', evidence_ids=['F004'])], actions=[])
    reference = context['_references']['C_confirmation'][0]
    reference['device_id'] = 'another-line'
    with pytest.raises(ValueError, match='产线或周期'):
        validate_brief(output, context)
    reference['device_id'] = ''
    reference['production_date'] = '2026-08-19'
    with pytest.raises(ValueError, match='产线或周期'):
        validate_brief(output, context)
    reference['production_date'] = '2026-08-18'
    context['facts'].append(dict(id='F099', label='任务变化', value='下降 5.0%', source='上一可比周期'))
    with pytest.raises(ValueError, match='变化方向'):
        validate_brief(output | {'summary': '任务数上升5.0%。'}, context)


@pytest.fixture
def duration_context():
    from datetime import date, datetime
    from decimal import Decimal
    from app.models.domain import WorkshopRecord
    from app.services.report_analysis.common import normalize_analysis_context
    row = WorkshopRecord('task-A', 'P', None, None, 'program', Decimal('10'), Decimal('0'), Decimal('3600'),
        production_date=date(2026, 11, 10), production_time=datetime(2026, 11, 10, 10, 30))
    return normalize_analysis_context(build_context('2026-11-10', [row]))[0]


@pytest.mark.parametrize('summary', ['任务耗时约60分钟。', '任务耗时1小时。', '任务耗时3600秒。', '任务耗时01:00:00。',
                                      '建议分2步核查。', '重点核对这2类记录。'])
def test_brief_accepts_equivalent_duration_and_prose_numbering(duration_context, summary):
    from app.services.report_analysis.brief_validation import validate_brief
    assert validate_brief(draft() | {'summary': summary}, duration_context)['summary'] == summary


@pytest.mark.parametrize('summary', ['本期有2项任务。', '任务耗时59分钟。', '任务耗时约90分钟。', '任务耗时10.5小时。'])
def test_brief_rejects_wrong_counts_durations_and_clock_times(duration_context, summary):
    from app.services.report_analysis.brief_validation import validate_brief
    with pytest.raises(ValueError, match='数量|时长'):
        validate_brief(draft() | {'summary': summary}, duration_context)


def test_internal_fact_ids_do_not_authorize_business_numbers():
    from app.services.report_analysis.brief_validation import _numbers
    assert _numbers('F002 F012 Cutter02') == set()
    assert _numbers('待确认2项，覆盖率40.0%') == {2, 40}


@pytest.mark.parametrize('summary', ['建议分2个阶段核查。', '建议分为 2 个阶段进行核对。',
                                      '可按2阶段开展排查。'])
def test_brief_accepts_small_procedural_stage_counts(summary):
    from app.services.report_analysis.brief_validation import validate_brief
    context = build_context('2026-11-09', [])
    assert validate_brief(draft() | {'summary': summary}, context)['summary'] == summary


@pytest.mark.parametrize('summary', ['存在2处待核实记录。', '涉及4类问题。', '生产分为2个阶段。',
                                      '建议分999999个阶段核查。'])
def test_procedural_exemptions_do_not_authorize_business_counts(summary):
    from app.services.report_analysis.brief_validation import validate_brief
    with pytest.raises(ValueError, match='数量'):
        validate_brief(draft() | {'summary': summary}, build_context('2026-11-09', []))


def test_brief_accepts_minutes_and_decimal_hours_from_authoritative_seconds(duration_context):
    from app.services.report_analysis.brief_validation import validate_brief
    context = duration_context
    context['tables'][0]['rows'][0]['work_time'] = 3540
    assert validate_brief(draft() | {'summary': '该任务耗时59分钟。'}, context)
    context['tables'][0]['rows'][0]['work_time'] = 5400
    assert validate_brief(draft() | {'summary': '该任务耗时1.5小时。'}, context)


@pytest.mark.parametrize(('seconds', 'summary'), [
    ([3600, 3560], '切割时长分别约60分钟和59分钟。'),
    ([3600, 3560, 3500], '切割时长分别约60分钟、59分钟及58分钟。'),
    ([7160], '该任务耗时1小时59分钟20秒。'),
    ([7160], '该任务耗时1 小时 59 分 20 秒。'),
    ([7160], '该任务耗时约1小时59分钟。'),
    ([7160], '该任务耗时119分钟20秒。'),
    ([5400], '该任务耗时1小时30分钟。'),
])
def test_brief_accepts_compound_durations_and_shared_approximation(duration_context, seconds, summary):
    from app.services.report_analysis.brief_validation import validate_brief
    duration_context['facts'] = []
    duration_context['tables'] = [dict(name='时长汇总', rows=[dict(work_time=value) for value in seconds])]
    assert validate_brief(draft() | {'summary': summary}, duration_context)['summary'] == summary


@pytest.mark.parametrize('summary', [
    '任务耗时1小时59分钟。',
    '任务耗时约1小时58分钟。',
    '任务耗时约1小时59分钟50秒。',
    '任务耗时约60分钟和58分钟。',
    '任务耗时约60分钟。另一任务耗时59分钟。',
    '任务耗时约60分钟，另一个任务耗时59分钟。',
    '任务耗时约60分钟，另有2项待确认。',
])
def test_brief_keeps_duration_precision_and_approximation_scope(duration_context, summary):
    from app.services.report_analysis.brief_validation import validate_brief
    duration_context['facts'] = []
    duration_context['tables'] = [dict(name='时长汇总', rows=[dict(work_time=value) for value in [3600, 3560, 7160]])]
    with pytest.raises(ValueError, match='时长|数量'):
        validate_brief(draft() | {'summary': summary}, duration_context)
