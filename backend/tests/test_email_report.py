from dataclasses import replace
from contextlib import closing
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.services.automation_runtime import AutomationRuntime
from app.services.automation_service import AutomationService
from app.services.email_report import EmailReport
from app.services.email_service import EmailService, EmailDeliveryFailure
from app.services.pdf_service import PdfService, PdfRenderError
from app.services.report_service import ReportService


@pytest.mark.parametrize("attach", [True, False])
def test_email_contains_html_related_images_and_optional_original_attachment(tmp_path, monkeypatch, attach):
    sent = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_message(self, message):
            sent.append(BytesParser(policy=policy.default).parsebytes(message.as_bytes()))
            return {}

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", SMTP)
    cfg = replace(settings, project_root=tmp_path, smtp_enabled=True, smtp_host="smtp.test",
                  smtp_from_email="report@example.com", smtp_username="", smtp_use_tls=False, smtp_use_ssl=False)
    images = {f"report-page-{number}@test": b"\x89PNG\r\n\x1a\n" + bytes([number]) for number in (1, 2)}
    renderer = SimpleNamespace(render_email=lambda path: EmailReport(
        '<h1>车间日报</h1>' + ''.join(f'<img src="cid:{cid}">' for cid in images), images))
    original = b"<html>original report with scripts</html>"
    delivery = {"message_id": "<stable@test>", "recipients": ["manager@example.com"], "subject": "日报",
                "schedule_name": "车间日报", "report_date": "2026-11-08", "filename": "车间日报.html"}
    service = EmailService(cfg, pdf_service=renderer)
    assert service.send(delivery, original if attach else None, report_path=tmp_path / "report.html") == "<stable@test>"
    message = sent[0]
    assert "车间日报" in message.get_body(preferencelist=("plain",)).get_content()
    assert all(f'src="cid:{cid}"' in message.get_body(preferencelist=("html",)).get_content() for cid in images)
    related = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(related) == len(images)
    for part, (cid, image) in zip(related, images.items()):
        assert part["Content-ID"] == f"<{cid}>"
        assert part.get_content_disposition() == "inline"
        assert part.get_payload(decode=True) == image
    attachments = list(message.iter_attachments())
    assert len(attachments) == int(attach)
    if attach:
        assert attachments[0].get_filename() == "车间日报.html"
        assert attachments[0].get_payload(decode=True) == original


def test_render_failure_is_retryable_and_does_not_connect_to_smtp(tmp_path, monkeypatch):
    def fail(path):
        raise PdfRenderError("Chromium failed")

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", lambda *a, **k: pytest.fail("SMTP must not be contacted"))
    cfg = replace(settings, smtp_enabled=True, smtp_host="smtp.test", smtp_from_email="report@example.com")
    email = EmailService(cfg, pdf_service=SimpleNamespace(render_email=fail))
    with pytest.raises(EmailDeliveryFailure, match="尚未提交 SMTP"):
        email.send({"recipients": ["a@example.com"], "subject": "日报", "schedule_name": "日报",
                    "report_date": "2026-11-08"}, None, report_path=tmp_path / "report.html")


def test_retry_wait_starts_after_slow_report_preparation(tmp_path, monkeypatch):
    from tests.automation_fixtures import NOW

    ticks = iter([100.0, 220.0])
    monkeypatch.setattr('app.services.automation_service.monotonic', lambda: next(ticks))
    cfg = replace(settings, project_root=tmp_path, email_retry_base_seconds=60)
    cfg.reports_dir.mkdir()
    (cfg.reports_dir / 'r.html').write_text('<h1>report</h1>')
    failures = []
    service = object.__new__(AutomationService)
    service.settings = cfg
    service.runtime = AutomationRuntime(cfg)
    service.repository = SimpleNamespace(
        claim_delivery=lambda now: {'delivery_id': 'd1', 'attempt_count': 1, 'relative_path': 'r.html', 'attach_report': False, 'ai_status': 'success'},
        mark_delivery_failed=lambda *a, **k: failures.append(k),
    )

    def render_timeout(*args, **kwargs):
        raise EmailDeliveryFailure('report rendering timed out')

    service.email_service = SimpleNamespace(send=render_timeout)
    assert service.process_next_email(NOW)
    from datetime import timedelta
    assert failures[0]['now'] == (NOW + timedelta(seconds=120)).isoformat(timespec='seconds')
    assert failures[0]['next_attempt_at'] == (NOW + timedelta(seconds=180)).isoformat(timespec='seconds')


def test_large_email_keeps_complete_continuous_pdf_without_rebuilding_table_cells(tmp_path):
    import pypdfium2 as pdfium
    path = tmp_path / 'large.html'
    row = '<tr>' + '<td>工程号-0001</td>' * 12 + '</tr>'
    path.write_text('<html><head><style>body{margin:0;color:#203b4d;font:14px/1.6 Arial}table{border-collapse:collapse;width:100%}td{height:42px;padding:8px 10px;border-bottom:1px solid #e0e8ec;white-space:nowrap}</style></head><body><table><tbody>' + row * 1000 + '</tbody></table></body></html>', encoding='utf-8')
    service = PdfService(operation_timeout_seconds=30)
    email = service.render_email(path)
    with pdfium.PdfDocument(service.render(path)) as document:
        assert len(document) == 1 and len(email.images) > 1
        count = 0
        for page in document:
            with closing(page), closing(page.get_textpage()) as textpage:
                count += textpage.get_text_range().count('工程号-0001')
        assert count == 12000
    assert email.html.count('<img') == len(email.images)
    assert len(email.html.encode('utf-8')) < 100_000


def test_email_pdf_pages_keep_long_tables_without_client_responsive_styles(tmp_path):
    import base64
    import pypdfium2 as pdfium
    from playwright.sync_api import sync_playwright
    path = tmp_path / 'scrolling-report.html'
    rows = ''.join(f'<tr><td>记录 {index}</td></tr>' for index in range(60))
    path.write_text('''<style>.data-chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
        .table-wrap{height:300px;max-height:300px;overflow:auto}td{height:30px}
        @media(max-width:760px){.data-chart-grid{grid-template-columns:1fr}}
        @media print{.data-chart-grid{display:block}.table-wrap{height:auto;max-height:none;overflow:visible}}</style>
        <div class="data-chart-grid"><div class="table-wrap"><table>''' + rows + '''</table></div>
        <div>图表位置</div></div><p class="mail-tail">报表结束</p>''', encoding='utf-8')
    service = PdfService(operation_timeout_seconds=30)
    email = service.render_email(path)
    with pdfium.PdfDocument(service.render(path)) as document:
        assert len(document) == 1 and email.images
        with closing(document[len(document) - 1]) as last, closing(last.get_textpage()) as textpage:
            assert '记录 59' in textpage.get_text_range()
    preview = email.html
    for cid, content in email.images.items():
        preview = preview.replace(f'cid:{cid}', 'data:image/png;base64,' + base64.b64encode(content).decode('ascii'))
    assert 'display:contents' not in email.html
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            for responsive in [True, False]:
                page.set_content(preview)
                if not responsive:
                    page.locator('style').evaluate_all('nodes => nodes.forEach(node => node.remove())')
                for width in [1440, 375]:
                    page.set_viewport_size(dict(width=width, height=900))
                    assert page.locator('img').count() == len(email.images)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    images = page.locator('img').all()
                    assert all(images[index + 1].bounding_box()['y'] >= image.bounding_box()['y'] + image.bounding_box()['height']
                               for index, image in enumerate(images[:-1]))
        finally:
            browser.close()


@pytest.mark.parametrize("relative_path", ["r.html", "../outside.html", "missing.html"])
@pytest.mark.parametrize("attach", [True, False])
def test_automation_prepares_body_independently_of_attachment_and_rejects_invalid_paths(tmp_path, relative_path, attach):
    cfg = replace(settings, project_root=tmp_path)
    cfg.reports_dir.mkdir()
    report = cfg.reports_dir / "r.html"
    report.write_text("<h1>日报</h1>", encoding="utf-8")
    (tmp_path / "outside.html").write_text("private")
    sent, failed = [], []
    delivery = {"delivery_id": "d1", "attempt_count": 1, "relative_path": relative_path, "attach_report": attach, 'ai_status': 'success'}
    service = object.__new__(AutomationService)
    service.settings = cfg
    service.runtime = AutomationRuntime(cfg)
    service.repository = SimpleNamespace(
        claim_delivery=lambda now: delivery,
        mark_delivery_sent=lambda *a, **k: None,
        mark_delivery_failed=lambda *a, **k: failed.append(k),
        mark_delivery_unknown=lambda *a, **k: pytest.fail("preparation is safe to retry"),
    )
    service.email_service = SimpleNamespace(send=lambda *a, **k: sent.append((a, k)) or "<id@test>")
    from tests.automation_fixtures import NOW
    assert service.process_next_email(NOW)
    if relative_path == "r.html":
        assert not failed
        assert sent[0][1]["report_path"] == report
        assert sent[0][0][1] == (report.read_bytes() if attach else None)
    else:
        assert failed and not sent


@pytest.mark.parametrize("report_type", ["leadership", "workshop", "equipment"])
@pytest.mark.parametrize(("empty", "with_brief"), [(False, False), (True, False), (False, True)])
def test_archived_report_becomes_pdf_page_email(tmp_path, report_type, empty, with_brief):
    root = Path(__file__).resolve().parents[2]
    cfg = replace(settings, project_root=tmp_path, data_source_mode="demo")
    renderer = ReportService(cfg)
    now = datetime(2026, 11, 9, 9, tzinfo=ZoneInfo("Asia/Shanghai"))
    count = 0 if empty else 1
    totals = dict(task_count=count, cut_length_m=120.5, groove_length_m=10, work_time_seconds=3600)
    remark = "长备注-工程资料完整保留-" * 12
    record = dict(task_id="TASK-001", project_code="P-001", ship_no="测试船名", segment_name="分段01",
                  program_no="程序01", cut_length=120.5, groove_length=10, work_time=3600,
                  production_time="2026-11-08T09:00:00", confirmed_by="张工", remark=remark,
                  electricity_consumption=None, gas_consumption=None)
    chart = {"animation": False, "xAxis": {"data": ["11-08"]}, "yAxis": {},
             "series": [{"type": "bar", "data": [120.5]}]}
    report = {
        "meta": dict(report_type=report_type, title='测试生产报告 "<邮件>"', report_id="report-test",
                     period_start="2026-11-08", period_end="2026-11-08", generated_at=now.isoformat(),
                     system_version="test", data_updated_at=now.isoformat(), custom_period=False),
        "filters": {"project_code": None, "ship_no": None},
        "leadership": dict(current_month_total=totals, twelve_month_total=totals, daily_records=[] if empty else [dict(totals, period_start="2026-11-08", remark=remark)],
                           monthly_records=[] if empty else [dict(totals, period_start="2026-11-01", remark="月度统计")]),
        "summary": dict(task_count=count, total_cut_length=120.5, total_groove_length=10, total_work_time=3600,
                        avg_cut_length=120.5, avg_groove_length=10, avg_work_time=3600),
        "workshop_records": [] if empty else [record], "worker_summaries": [],
        "equipment_summary": dict(task_count=count, total_work_time=3600, total_electricity_consumption=None,
                                  total_gas_consumption=None, electricity_record_count=0, gas_record_count=0),
        "equipment_records": [] if empty else [record],
        "units": {"electricity": "kWh", "gas": "m³"},
        "energy_month": dict(rows=[], total_task_count=count, total_work_time_seconds=3600,
                             summary={"electricity": {"value": None}, "gas": {"value": None}}),
        "charts": {key: chart for key in ["daily_trend", "monthly_trend", "length_combo", "equipment_detail", "equipment_month"]},
        "analysis": dict(mode="unavailable", generated_at=now.isoformat(), cached=False,
                         facts=[] if empty else [dict(id=f"F{index:03}", label=label, value=value, source="应隐藏的来源说明")
                            for index, (label, value) in enumerate([
                                ("当月任务数", "1051 项"), ("当月切割长度", "53,586.4 米"),
                                ("当月切割时长", "334:07:06"), ("任务数比上一周期", "下降 7.6%"),
                                ("任务数比去年同期", "上升 46.2%"),
                                ("工程号与任务较多的日期", "工程号未填写 984 项，任务较多的日期 1 天"),
                            ], 1)],
                         overview="分析说明 <script>不能执行</script>", features="特征", alerts="关注点", suggestions="建议"),
    }
    if with_brief:
        reference = dict(kind='project', id='P-001', label='P-001') if report_type == 'leadership' else dict(
            kind='task', id='TASK-001', label='TASK-001', project_code='P-001', program_no='程序01')
        if report_type == 'leadership':
            report['leadership']['daily_records'].append(dict(totals, period_start='2026-11-09', remark='次日记录'))
            report['leadership']['project_distribution'] = [
                dict(project_name='P-001', task_count=1, cut_length_m=120.5, work_time_seconds=3600)]
            report['analysis']['facts'].append(dict(id='F007', label='当月坡口长度', value='10.0 米'))
        report['analysis'].update(mode='model', brief=dict(
            scope=dict(period_start='2026-11-08', period_end='2026-11-08',
                       comparison_start='2026-11-07', comparison_end='2026-11-07'),
            headline='本期生产简报', summary='生产记录已汇总。\n当前周期对比仅作进度参考。',
            key_metric_ids=['F001', 'F002', 'F003'],
            findings=[
                dict(candidate_id='C_comparison', title='生产规模变化', detail='本期统计供核对。', references=[], reference_count=0),
                dict(candidate_id='C_project', title='可核对的关联记录', detail='见下方依据。', references=[reference], reference_count=1),
            ],
            actions=[dict(title='核对生产记录', detail='确认来源与日期。')],
            data_quality=['工人工时仅包含已确认任务。', '当前统计周期尚未结束，仅作进度参考。']))
        if report_type == 'workshop':
            record.update(cut_length=0, groove_length=0, work_time=51)
            report['summary'].update(total_cut_length=0, total_groove_length=0, total_work_time=51,
                                     avg_cut_length=0, avg_groove_length=0, avg_work_time=51)
            quality = '有用时但切割长度为零，需核对原始计数及试切记录。'
            report['analysis']['facts'].append(dict(id='F020', label='任务数据待核查', value=quality))
            report['analysis']['brief']['findings'].append(dict(candidate_id='C_task_quality',
                title='核对零长度任务', detail=quality, evidence_ids=['F020'], reference_count=1,
                references=[dict(reference, label='程序01 · 切割长度 0.0 米 · 用时 00:00:51')]))
    html = renderer.environment.get_template("report.html.j2").render(
        report=report, echarts_source=(root / "vendor/echarts.min.js").read_text(encoding="utf-8"),
        demo_mode=True, device_name="切割机01", energy_connected=False,
    )
    path = tmp_path / "report.html"
    path.write_text(html, encoding="utf-8")
    service = PdfService(operation_timeout_seconds=30)
    email = service.render_email(path)
    reference_pdf = service.render(path)
    assert path.read_text(encoding="utf-8") == html
    assert "<script" not in email.html and "<canvas" not in email.html
    assert "http://" not in email.html and "https://" not in email.html
    assert '&quot;&lt;邮件&gt;&quot;' in email.html

    import base64
    from io import BytesIO
    import pypdfium2 as pdfium
    from PIL import Image, ImageChops
    from playwright.sync_api import sync_playwright

    texts = []
    with pdfium.PdfDocument(reference_pdf) as document:
        assert len(document) == 1 and email.images
        assert list(email.images) == [f"report-page-{index + 1}@cutting-report" for index in range(len(email.images))]
        fragments = []
        for index, (cid, content) in enumerate(email.images.items()):
            assert f'src="cid:{cid}"' in email.html
            assert f'连续内容 {index + 1}' in email.html
            with Image.open(BytesIO(content)) as actual:
                assert actual.format == "PNG" and actual.height <= 4096
                fragments.append(actual.convert('RGB'))
        with closing(document[0]) as page, closing(page.get_textpage()) as textpage:
            texts.append(textpage.get_text_range())
            with closing(page.render(scale=2)) as bitmap, bitmap.to_pil() as expected:
                combined = Image.new('RGB', (fragments[0].width, sum(image.height for image in fragments)))
                top = 0
                for fragment in fragments:
                    combined.paste(fragment, (0, top)); top += fragment.height
                assert combined.size == expected.size
                assert ImageChops.difference(combined, expected.convert('RGB')).getbbox() is None
                combined.close()
        for fragment in fragments:
            fragment.close()
    assert 'padding:0;border:0;font-size:0;line-height:0' in email.html and '共 1 页' not in email.html
    text = "".join(texts).replace(" ", "").replace("\r", "").replace("\n", "")
    assert "测试生产报告" in text and "切割机01" in text
    assert "演示数据" in text and "非生产统计" in text and "报告结束" in text
    assert "应隐藏的来源说明" not in text
    if report_type == 'leadership':
        assert '近12个月生产趋势' in text and 'AI智能生产洞察' in text
    if with_brief:
        assert text.count('生产执行摘要') == 1 and text.count('本期生产简报') == 1
        assert text.count('生产记录已汇总。当前周期对比仅作进度参考。') == 1
        assert '可核对的关联记录' in text
    else:
        assert "智能分析暂不可用" in text
    if empty and report_type in {"leadership", "workshop"}:
        assert "暂无" in text
    if not empty and report_type in {"leadership", "workshop"}:
        assert "备注" not in text and remark not in text
        if with_brief and report_type == "leadership":
            assert "次日记录" not in text
    if not empty and report_type == "workshop":
        assert "TASK-001" in text
        if with_brief:
            assert text.count('有用时但切割长度为零，需核对原始计数及试切记录。') == 1
            assert '核对零长度任务' in text and '用时00:00:51' not in text
    if not empty and report_type == "equipment":
        assert "测试船名" in text

    preview = email.html
    for cid, content in email.images.items():
        preview = preview.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(content).decode("ascii"))
    (tmp_path / "email-preview.html").write_text(preview, encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(preview)
            page.wait_for_function("Array.from(document.images).every(image => image.complete && image.naturalWidth > 0)")
            for width in [1440, 375]:
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert page.locator("img").count() == len(email.images)
                assert page.locator("img").evaluate_all("images => images.every(image => Math.abs(image.width / image.height - image.naturalWidth / image.naturalHeight) < 0.01)")
                page.screenshot(path=str(tmp_path / f"email-{width}.png"), full_page=True)
        finally:
            browser.close()


def test_pdf_page_conversion_failure_releases_capacity_and_removes_temporary_pdf(tmp_path, monkeypatch):
    temporary_pdfs = []

    def invalid_pdf(source, target):
        target.write_bytes(b"not a PDF")
        temporary_pdfs.append(target)

    monkeypatch.setattr(PdfService, "_render_with_playwright", invalid_pdf)
    path = tmp_path / "report.html"
    path.write_text("<h1>original report</h1>")
    service = PdfService(max_waiters=0)
    with pytest.raises(PdfRenderError, match="邮件正文生成失败"):
        service.render_email(path)
    assert temporary_pdfs and all(not path.exists() for path in temporary_pdfs)
    assert path.read_text() == "<h1>original report</h1>"
    with service._render_slot():
        pass
