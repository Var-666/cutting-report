from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

from app.models.domain import to_primitive
from app.services.chart_service import ChartService
from app.modules.cutting.leadership import LeadershipReportService
from app.services.pdf_service import PdfService
from app.services.report_analysis.leadership import build_context
from app.services.report_service import ReportService
from tests.line_fixtures import cutting_settings


def test_portrait_report_preserves_full_daily_table_and_historical_long_analysis(tmp_path):
    import pypdfium2 as pdfium

    root = Path(__file__).resolve().parents[2]
    cfg = cutting_settings(project_root=root, data_source_mode='demo')
    leadership = LeadershipReportService(cfg).fetch(date(2026, 10, 1))
    daily = [replace(leadership.daily_records[0], period_start=date(2026, 10, 31) - timedelta(days=i),
                     remark='不要输出的备注') for i in range(31)]
    total = replace(daily[-1], **{key: sum(getattr(row, key) for row in daily) for key in (
        'task_count', 'cut_length_m', 'groove_length_m', 'work_time_seconds')})
    leadership = replace(leadership, daily_records=daily, current_month_total=total)
    context = build_context(leadership, leadership.monthly_records)
    detail = '长分析必须保留完整文本，不能因固定页数而截断。' * 100 + '分析末尾校验标记'
    report = dict(
        meta=dict(report_type='leadership', title='切割生产领导月报', report_id='portrait-test',
                  period_start='2026-10-01', period_end='2026-10-31', generated_at='2026-11-13T11:00:00+08:00',
                  system_version='test', custom_period=False),
        filters=dict(project_code=None, ship_no=None), leadership=to_primitive(leadership),
        charts=ChartService().build_leadership(leadership),
        analysis=dict(mode='model', status='success', cached=True, cache_note='沿用历史成功分析，请核对分析日期。',
                      facts=context['facts'], generated_at='2026-11-12T10:00:00+08:00',
                      brief=dict(headline='历史分析标题校验标记', summary='历史成功分析的原始内容。',
                                 scope=context['scope'], findings=[dict(title='完整关联发现', detail=detail,
                                    references=[dict(kind='project', id='DEMO-A', label='工程校验标记')], reference_count=1)],
                                 actions=[dict(title='行动校验标记', detail='核对生产记录。')],
                                 data_quality=context['data_quality'])))
    html = ReportService(cfg).environment.get_template('report.html.j2').render(
        report=report, echarts_source=cfg.echarts_vendor_path.read_text(), demo_mode=True,
        device_name='20KW切割机', energy_connected=False)
    source = tmp_path / 'long-report.html'
    source.write_text(html, encoding='utf-8')
    pdf = PdfService(operation_timeout_seconds=60).render(source)
    texts = []
    with pdfium.PdfDocument(pdf) as document:
        assert len(document) == 1
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as text:
                assert page.get_width() < page.get_height()
                texts.append(text.get_text_range().replace(' ', '').replace('\r', '').replace('\n', ''))
    printed = ''.join(texts)
    assert all(f'2026年10月{day:02d}日' in printed for day in range(1, 32))
    assert '分析末尾校验标记' in printed and '行动校验标记' in printed
    assert '工程校验标记' not in printed
    assert printed.count('历史分析标题校验标记') == 1
    assert '沿用历史成功分析' not in printed and '不要输出的备注' not in printed
    assert '2026-11-12' in printed
    assert '报告结束' in printed
