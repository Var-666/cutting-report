import pytest

from app.services.analysis_display import analysis_reference_label
from app.services.report_service import ReportService
from tests.line_fixtures import cutting_settings


@pytest.mark.parametrize('reference,expected', [
    ({'kind': 'task', 'id': '30984', 'label': '30984', 'program_no': '806C-01.gen', 'project_code': 'CMYZ0170'},
     '程序 806C-01.gen · 工程 CMYZ0170'),
    ({'kind': 'task', 'id': '30984', 'label': '程序 A · 分段 806C'}, '程序 A · 分段 806C'),
    ({'kind': 'task', 'id': '30984', 'label': '30984', 'production_date': '2026-11-12'},
     '2026-11-12 切割任务（业务信息未填写）'),
    ({'kind': 'task', 'id': '30984', 'label': '30984'}, '切割任务（业务信息未填写）'),
    ({'kind': 'project', 'id': 'P-01', 'label': 'P-01'}, 'P-01'),
])
def test_reference_label_keeps_business_context_instead_of_raw_task_id(reference, expected):
    assert analysis_reference_label(reference) == expected


def test_archived_analysis_formats_old_cached_references_and_escapes_names(tmp_path):
    service = ReportService(cutting_settings(project_root=tmp_path))
    reference = dict(kind='task', id='30984', label='30984', program_no='<程序A>.gen', project_code='CMYZ0170')
    references = [reference, *[dict(kind='task', id=str(index), label=f'完整关联程序{index}.gen') for index in range(2, 6)]]
    brief = dict(scope={}, headline='生产简报', summary='核对切割记录。', key_metric_ids=[], actions=[], data_quality=[],
        findings=[dict(candidate_id='C_confirmation', title='待工人确认', detail='核对确认归属。',
                       references=references, reference_count=len(references))])
    template = service.environment.from_string('''{% from 'report_analysis.html.j2' import render_analysis with context %}
        {{ render_analysis(analysis, 'workshop', report, '切割一线', '2026-11-12', '2026-11-12') }}''')
    html = template.render(analysis=dict(brief=brief, facts=[], generated_at='2026-11-12', cached=True,
        cache_note='数据已更新，当前沿用历史成功分析；原生成时间和分析依据保持不变。'), report={})
    assert 'CMYZ0170' in html and '&lt;程序A&gt;.gen' in html
    assert '30984' not in html and '<程序A>' not in html
    assert all(f'完整关联程序{index}.gen' in html for index in range(2, 6))
    assert '沿用历史成功分析' not in html and '2026-11-12' in html
