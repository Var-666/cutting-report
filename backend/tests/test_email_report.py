from dataclasses import replace
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
    image = b"\x89PNG\r\n\x1a\nimage"
    renderer = SimpleNamespace(render_email=lambda path: EmailReport('<h1>车间日报</h1><img src="cid:chart@test">', {"chart@test": image}))
    original = b"<html>original report with scripts</html>"
    delivery = {"message_id": "<stable@test>", "recipients": ["manager@example.com"], "subject": "日报",
                "schedule_name": "车间日报", "report_date": "2026-11-08", "filename": "车间日报.html"}
    service = EmailService(cfg, pdf_service=renderer)
    assert service.send(delivery, original if attach else None, report_path=tmp_path / "report.html") == "<stable@test>"
    message = sent[0]
    assert "车间日报" in message.get_body(preferencelist=("plain",)).get_content()
    assert 'src="cid:chart@test"' in message.get_body(preferencelist=("html",)).get_content()
    related = [part for part in message.walk() if part.get_content_type() == "image/png"]
    assert len(related) == 1
    assert related[0]["Content-ID"] == "<chart@test>"
    assert related[0].get_content_disposition() == "inline"
    assert related[0].get_payload(decode=True) == image
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
        claim_delivery=lambda now: {'delivery_id': 'd1', 'attempt_count': 1, 'relative_path': 'r.html', 'attach_report': False},
        mark_delivery_failed=lambda *a, **k: failures.append(k),
    )

    def render_timeout(*args, **kwargs):
        raise EmailDeliveryFailure('report rendering timed out')

    service.email_service = SimpleNamespace(send=render_timeout)
    assert service.process_next_email(NOW)
    from datetime import timedelta
    assert failures[0]['now'] == (NOW + timedelta(seconds=120)).isoformat(timespec='seconds')
    assert failures[0]['next_attempt_at'] == (NOW + timedelta(seconds=180)).isoformat(timespec='seconds')


def test_large_email_omits_repeated_default_styles_and_preserves_all_cells(tmp_path):
    path = tmp_path / 'large.html'
    row = '<tr>' + '<td>工程号-0001</td>' * 12 + '</tr>'
    path.write_text('<html><head><style>body{margin:0;color:#203b4d;font:14px/1.6 Arial}table{border-collapse:collapse;width:100%}td{height:42px;padding:8px 10px;border-bottom:1px solid #e0e8ec;white-space:nowrap}</style></head><body><table><tbody>' + row * 1000 + '</tbody></table></body></html>', encoding='utf-8')
    email = PdfService(operation_timeout_seconds=30).render_email(path)
    assert email.html.count('<td') == 12000
    assert email.html.count('工程号-0001') == 12000
    assert 'box-shadow: none' not in email.html and 'max-height: none' not in email.html
    assert len(email.html.encode('utf-8')) < 3_000_000


@pytest.mark.parametrize("relative_path", ["r.html", "../outside.html", "missing.html"])
@pytest.mark.parametrize("attach", [True, False])
def test_automation_prepares_body_independently_of_attachment_and_rejects_invalid_paths(tmp_path, relative_path, attach):
    cfg = replace(settings, project_root=tmp_path)
    cfg.reports_dir.mkdir()
    report = cfg.reports_dir / "r.html"
    report.write_text("<h1>日报</h1>", encoding="utf-8")
    (tmp_path / "outside.html").write_text("private")
    sent, failed = [], []
    delivery = {"delivery_id": "d1", "attempt_count": 1, "relative_path": relative_path, "attach_report": attach}
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
def test_archived_report_becomes_readable_email(tmp_path, report_type, empty, with_brief):
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
        "meta": dict(report_type=report_type, title="测试生产报告", report_id="report-test",
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
        report['analysis'].update(mode='model', brief=dict(
            scope=dict(period_start='2026-11-08', period_end='2026-11-08',
                       comparison_start='2026-11-07', comparison_end='2026-11-07'),
            headline='本期生产简报', summary='生产记录已汇总。',
            key_metric_ids=['F001', 'F002', 'F003'], findings=[], actions=[],
            data_quality=['工人工时仅包含已确认任务。']))
    html = renderer.environment.get_template("report.html.j2").render(
        report=report, echarts_source=(root / "vendor/echarts.min.js").read_text(encoding="utf-8"),
        demo_mode=True, device_name="切割机01", energy_connected=False,
    )
    if empty and report_type == "workshop":
        # A template style change must reach mail without a second theme update.
        html = html.replace("</head>", "<style>.page{width:min(1280px,calc(100% - 40px));margin-top:28px;margin-bottom:28px}.cover-title h1{font-size:31px;color:#d9eff9}.metric strong{font-size:23px}.analysis-card.features{background:#e6f2ea}</style></head>")
    path = tmp_path / "report.html"
    path.write_text(html, encoding="utf-8")
    # Exercise the same isolated Chromium path and capacity used by Automation.
    email = PdfService(operation_timeout_seconds=30).render_email(path)
    expected_images = (0 if empty else 2) if report_type == "leadership" else ((0 if empty else 1) if report_type == "workshop" else (1 if empty else 2))
    assert len(email.images) == expected_images
    for cid, content in email.images.items():
        assert f'cid:{cid}' in email.html
        assert content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "测试生产报告" in email.html and "切割机01" in email.html
    assert "演示数据 · 非生产统计" in email.html
    if with_brief:
        assert all(text in html and text in email.html for text in
                   ['本期生产简报', '分析依据', '2026-11-07', '工人工时仅包含已确认任务。', '任务数比上一周期'])
    else:
        assert "智能分析暂不可用" in email.html and "&lt;script&gt;不能执行&lt;/script&gt;" in email.html
    assert "报告结束" in email.html
    assert "<script" not in email.html and "<canvas" not in email.html and "<link" not in email.html
    assert path.read_text(encoding="utf-8") == html
    assert "应隐藏的来源说明" not in html and "应隐藏的来源说明" not in email.html and "来源：" not in email.html
    if not empty and report_type in {"leadership", "workshop"}:
        assert email.html.count(remark) == 1, "print-only tables must not duplicate records"
    if not empty and report_type in {"workshop", "equipment"}:
        assert email.html.count("测试船名") == 1
    if empty and report_type in {"leadership", "workshop"}:
        assert "暂无" in email.html
    import base64
    from playwright.sync_api import sync_playwright

    preview = email.html
    for cid, content in email.images.items():
        preview = preview.replace(f"cid:{cid}", "data:image/png;base64," + base64.b64encode(content).decode("ascii"))
    (tmp_path / "email-preview.html").write_text(preview, encoding="utf-8")
    labels = {
        "leadership": ["完成任务", "切割长度", "坡口长度", "切割时长"],
        "workshop": ["任务数量", "总切割长度", "总坡口长度", "总切割时长"],
        "equipment": ["任务数量", "切割时长", "任务气耗合计", "任务电耗合计"],
    }[report_type]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            reference = browser.new_page()
            reference.set_content(html, wait_until="load")
            reference.wait_for_function("window.__REPORT_READY__ === true")
            page = browser.new_page()
            page.set_content(preview)
            assert page.locator("h2").all_text_contents() == reference.locator("h2").all_text_contents()
            for width in [1440, 1100, 820, 375]:
                reference.set_viewport_size({"width": width, "height": 900})
                page.set_viewport_size({"width": width, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if not empty:
                    for document in [reference, page]:
                        cards = document.locator('.analysis-fact')
                        assert cards.count() == (3 if with_brief else 6)
                        boxes = [card.bounding_box() for card in cards.all()]
                        columns = sum(abs(box['y'] - boxes[0]['y']) < 1 for box in boxes) if with_brief else (3 if width > 1000 else 2)
                        assert all(abs(box['y'] - boxes[0]['y']) < 1 for box in boxes[:columns])
                        if columns > 1:
                            assert boxes[1]['x'] - boxes[0]['x'] - boxes[0]['width'] >= 10
                        if len(boxes) > columns:
                            assert boxes[columns]['y'] - boxes[0]['y'] - boxes[0]['height'] >= 10
                        assert all(box['width'] > 80 for box in boxes)
                        assert cards.evaluate_all('nodes => nodes.every(node => node.scrollWidth <= node.clientWidth + 1)')
                assert abs(page.locator('.page').bounding_box()['width'] - reference.locator('.page').bounding_box()['width']) < 1
                assert page.locator('table:not([role="presentation"]) td').evaluate_all("cells => cells.every(cell => getComputedStyle(cell).whiteSpace === 'nowrap')")
                for selector in [".cover-brand", ".cover-title h1", ".cover-title p", ".metrics", ".metric strong", "h2", ".analysis-card.features", ".analysis-card.alerts", ".analysis-mode"]:
                    if with_brief and selector in {'.analysis-card.features', '.analysis-card.alerts'}:
                        continue
                    styles = "element => Object.fromEntries(['fontFamily','fontSize','fontWeight','color','backgroundColor','backgroundImage'].map(key => [key,getComputedStyle(element)[key]]))"
                    assert page.locator(selector).first.evaluate(styles) == reference.locator(selector).first.evaluate(styles), selector
                if page.locator('thead th').count():
                    assert page.locator('thead th').first.evaluate(styles) == reference.locator('thead th').first.evaluate(styles)
                    assert abs(page.locator('thead th').first.bounding_box()['height'] - reference.locator('thead th').first.bounding_box()['height']) < 1
                for original_table, email_table in zip(reference.locator('.table-wrap > table').all(),page.locator('.table-wrap > table').all()):
                    assert abs(original_table.bounding_box()['width'] - email_table.bounding_box()['width']) < 1
                for original_chart, email_chart in zip(reference.locator('.chart').all(), page.locator('.chart').all()):
                    assert abs(original_chart.bounding_box()['height'] - email_chart.bounding_box()['height']) < 1
                    assert email_chart.evaluate(styles) == original_chart.evaluate(styles)
                if width > 1000:
                    for original_grid, email_grid in zip(reference.locator('.data-chart-grid').all(), page.locator('.data-chart-grid').all()):
                        original_widths = original_grid.evaluate('node => Array.from(node.children,child => child.getBoundingClientRect().width)')
                        email_widths = email_grid.locator(':scope > table > tbody > tr > .email-cell').evaluate_all('nodes => nodes.map(node => node.getBoundingClientRect().width)')
                        assert all(abs(original - converted) < 1 for original, converted in zip(original_widths,email_widths))
                metrics = [page.get_by_text(label, exact=True).first.locator("..").bounding_box() for label in labels]
                original_metrics = [reference.get_by_text(label, exact=True).first.locator("..").bounding_box() for label in labels]
                original_accents = reference.locator('.metric').evaluate_all("nodes => nodes.map(node => getComputedStyle(node,'::before').backgroundColor)")
                assert page.locator('.metric').evaluate_all("nodes => nodes.map(node => getComputedStyle(node).borderTopColor)") == original_accents
                for metric, original_metric in zip(metrics, original_metrics):
                    assert abs(metric["height"] - original_metric["height"]) < 2
                assert max(item["width"] for item in metrics) - min(item["width"] for item in metrics) < 1
                assert abs(metrics[0]["y"] - metrics[1]["y"]) < 1
                assert abs(metrics[2]["y"] - metrics[3]["y"]) < 1
                if width > 1000:
                    assert abs(metrics[0]["y"] - metrics[3]["y"]) < 1
                    assert metrics[3]["x"] > metrics[2]["x"] > metrics[1]["x"] > metrics[0]["x"]
                else:
                    assert metrics[2]["y"] >= metrics[0]["y"] + metrics[0]["height"] - 1
                page.screenshot(path=str(tmp_path / f"email-{width}.png"), full_page=True)
        finally:
            browser.close()
