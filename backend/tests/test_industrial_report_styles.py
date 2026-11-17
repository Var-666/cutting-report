from contextlib import closing

import pytest

from app.services.pdf_service import PdfService
from tests.report_style_fixtures import BUSINESS_MARKER, STYLE_KINDS, style_report_html


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
                assert document.bounding_box()['width'] == pytest.approx(820 if width == 1440 else width - 16)
                assert document.locator('h1').first.evaluate('(el) => parseFloat(getComputedStyle(el).fontSize)') == pytest.approx(36 if width == 1440 else 25)
                assert document.get_by_text('生产执行摘要', exact=True).count() == 1
                assert document.locator('.lp-kpis').count() == 1
                assert document.locator('.lp-kpi strong').first.evaluate('(el) => parseFloat(getComputedStyle(el).fontSize)') == pytest.approx(23 * 4 / 3, abs=.001)
                assert BUSINESS_MARKER in page.locator('body').inner_text()
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
    email = service.render_email(source)
    assert email.images and '连续内容' in email.html
    assert 'padding:0;border:0;font-size:0;line-height:0' in email.html
    assert '第 1 页' not in email.html
