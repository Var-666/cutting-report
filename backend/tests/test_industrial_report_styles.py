from contextlib import closing

import pytest

from app.modules.reports.pdf import PdfService
from tests.report_style_fixtures import BUSINESS_MARKER, STYLE_KINDS, style_report_html


def test_disconnected_energy_notice_keeps_text_without_a_colored_strip(tmp_path):
    from playwright.sync_api import sync_playwright

    source = style_report_html('equipment', tmp_path, energy_connected=False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content(source.read_text(), wait_until='load')
            notice = page.locator('.notice')
            assert '能耗暂未接入' in notice.inner_text()
            for media, width in [('screen', 1440), ('screen', 375), ('print', 794)]:
                page.set_viewport_size(dict(width=width, height=900))
                page.emulate_media(media=media)
                assert notice.evaluate("""item => {
                    const style = getComputedStyle(item);
                    return style.backgroundColor === 'rgba(0, 0, 0, 0)' && style.borderLeftWidth === '0px';
                }""")
        finally:
            browser.close()


@pytest.mark.parametrize('kind', STYLE_KINDS)
def test_all_report_styles_keep_complete_html_pdf_and_continuous_email(tmp_path, kind):
    import pypdfium2 as pdfium
    from playwright.sync_api import sync_playwright

    source = style_report_html(kind, tmp_path)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.set_content(source.read_text(), wait_until='load')
            page.evaluate('async () => { if (window.prepareIndustrialReport) await window.prepareIndustrialReport(); }')
            for width in [1440, 375]:
                page.set_viewport_size(dict(width=width, height=900))
                page.evaluate('async () => { await window.prepareIndustrialReport(); }')
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (kind, width)
                assert page.locator('.industrial-brand:visible').count() == 1
                document = page.locator('.industrial-document:visible')
                assert document.locator('.lp-method').count() == 0
                assert document.bounding_box()['width'] == pytest.approx(820 if width == 1440 else width - 16)
                assert document.locator('h1').first.evaluate('(el) => parseFloat(getComputedStyle(el).fontSize)') == pytest.approx(36 if width == 1440 else 25)
                assert document.get_by_text('生产执行摘要', exact=True).count() == int(kind in {'leadership', 'workshop', 'alarm', 'custom'})
                assert document.locator('.lp-kpis').count() == 1
                assert document.locator('.lp-kpi strong').first.evaluate('(el) => parseFloat(getComputedStyle(el).fontSize)') == pytest.approx(23 * 4 / 3, abs=.001)
                assert BUSINESS_MARKER in page.locator('body').inner_text()
                if kind in {'workshop', 'equipment'}:
                    unknown = document.locator('.screen-only tbody').get_by_text('未知', exact=True)
                    assert unknown.evaluate('(el) => getComputedStyle(el).color') == 'rgb(153, 27, 27)'
                if kind == 'leadership':
                    assert document.get_by_text('领导简报结论', exact=True).count() == 1
                    assert document.get_by_text('优先核对工程归属。 确认后更新汇总。', exact=True).count() == 1
                    assert document.get_by_text('当月任务数', exact=True).count() == 0
                    assert document.get_by_text('工程号与任务较多的日期', exact=True).count() == 1
                elif kind == 'workshop':
                    priority = document.locator('.lp-analysis').get_by_text('需优先核查：1 项记录：发现负数数据', exact=False)
                    assert priority.bounding_box()['y'] > document.get_by_role(
                        'heading', name='核心生产指标（本期）', exact=False).bounding_box()['y']
                    assert document.get_by_role('heading', name='数据质量提示', exact=True).count() == 0
                    findings = document.locator('.lp-finding h3')
                    assert findings.filter(has_text='本期实际切割记录').inner_text() == '本期实际切割记录'
                    assert findings.filter(has_text='切割时间较长的任务').get_by_text('待核查', exact=True).evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgb(254, 243, 199)'
                elif kind == 'equipment':
                    assert document.get_by_text('匹配不全', exact=True).evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgb(254, 243, 199)'
                    assert '电耗已匹配 1/1 项' in document.inner_text()
                elif kind == 'alarm':
                    assert document.get_by_role('heading', name='核心告警指标（本次分析）', exact=True).count() == 1
                    assert document.locator('.lp-kpi strong').all_text_contents() == ['1', '1', '0', '0']
                    assert document.locator('table tbody tr').count() == 1
                    assert document.get_by_text('发现需要现场核查的告警记录。', exact=True).count() == 1
                    assert document.get_by_text('风险等级', exact=False).count() == 1
                    assert document.locator('.lp-context').get_by_text('严重', exact=True).evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgb(255, 241, 242)'
                    assert document.locator('.lp-finding').get_by_text('高', exact=True).evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgb(255, 237, 213)'
                    alarm_kpis = document.locator('.lp-kpi')
                    assert alarm_kpis.filter(has_text='严重').evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgb(255, 241, 242)'
                    assert alarm_kpis.filter(has_text='高').evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgba(0, 0, 0, 0)'
                elif kind.startswith('welding-'):
                    assert document.get_by_text('仅使用已记录的焊接生产数据。', exact=True).count() == 0
                    assert document.get_by_text('筛选条件：设备：0', exact=True).count() == 1
                    assert document.get_by_text('长度无效 1', exact=True).evaluate(
                        '(el) => getComputedStyle(el).backgroundColor') == 'rgb(254, 243, 199)'
                    assert '用时无效 0' in document.inner_text()
                elif kind == 'custom':
                    assert document.get_by_text('查询共 2 行 · 显示 1 行', exact=True).count() == 1
                assert page.locator('table:visible th,table:visible td').evaluate_all(
                    "cells => cells.every(cell => getComputedStyle(cell).whiteSpace === 'nowrap')")
                if kind == 'custom':
                    assert page.locator('.chart-card').bounding_box()['y'] > page.locator('.table-wrap').bounding_box()['y']
                page.screenshot(path=str(tmp_path / f'{kind}-{width}.png'), full_page=True)
            page.emulate_media(media='print')
            page.evaluate('async () => { await window.prepareIndustrialReport(); }')
            if kind == 'custom':
                assert page.locator('.chart-card').bounding_box()['y'] > page.locator('.table-wrap').bounding_box()['y']
            assert page.locator('table:visible').evaluate_all("""tables => tables.every(table => {
                const root = table.closest('.industrial-document,.leadership-print');
                return table.getBoundingClientRect().right <= root.getBoundingClientRect().right + 1;
            })""")
        finally:
            browser.close()
    assert not errors, errors
    service = PdfService(operation_timeout_seconds=60)
    pdf = service.render(source)
    with pdfium.PdfDocument(pdf) as document:
        assert len(document) == 1
        with closing(document[0]) as page, closing(page.get_textpage()) as textpage:
            assert page.get_width() == pytest.approx(210 * 72 / 25.4, abs=1)
            text = textpage.get_text_range().replace(' ', '').replace('\r', '').replace('\n', '')
            assert BUSINESS_MARKER in text and '报告结束' in text
            if kind in {'workshop', 'equipment'}:
                assert '未知' in text
            if kind == 'workshop':
                assert '优先核查' in text and '待核查' in text
    email = service.render_email(source)
    assert email.images and '连续内容' in email.html
    assert 'padding:0;border:0;font-size:0;line-height:0' in email.html
    assert '第 1 页' not in email.html
