from tests.runtime_factories import make_device_runtime
from tests.web_fixtures import embedded_app, replace_database_services
from tests.line_fixtures import as_line_services, runtime_line
from tests.line_fixtures import configuration
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.modules.reports import routes as reports
from app.api.auth import require_admin
from app.modules.reports.pdf import PdfRenderError, PdfService


@pytest.fixture
def report_client(tmp_path, monkeypatch):
    html = tmp_path / "saved.html"
    html.write_text("<h1>车间日报</h1>", encoding="utf-8")
    job = {"relative_path": html.name, "filename": "车间日报_2026-08-18.html", "status": "completed"}
    calls = []

    def render(path):
        calls.append(path)
        pdf = path.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-1.7\nreport content")
        return pdf

    app = embedded_app(configuration(tmp_path))
    app.include_router(reports.router)
    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    app.state.runtime.metadata = SimpleNamespace(get_job=lambda report_id: job if report_id == "saved" else None)
    replace_database_services(app, lines=as_line_services({'Cutter01': make_device_runtime(metadata=app.state.runtime.metadata)}))
    app.state.runtime.pdf_service = SimpleNamespace(render=render)
    # Settings resolves report files under project_root/reports.
    (tmp_path / "reports").mkdir()
    html = html.rename(tmp_path / "reports" / html.name)
    client = TestClient(app)
    return client, job, calls, html


def test_html_download_stays_unchanged_and_does_not_render_pdf(report_client):
    client, _, calls, html = report_client
    response = client.get('/api/v1/reports/saved/download?device_id=Cutter01')
    assert response.status_code == 200
    assert response.content == html.read_bytes()
    assert response.headers["content-type"].startswith("text/html")
    assert calls == []


def test_pdf_download_has_pdf_bytes_type_and_chinese_filename(report_client):
    client, _, calls, html = report_client
    response = client.get('/api/v1/reports/saved/download?format=pdf&device_id=Cutter01')
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert "车间日报_2026-08-18.pdf" in unquote(response.headers["content-disposition"])
    assert response.headers["cache-control"] == "private, no-store"
    assert calls == [html]


def test_pdf_render_failure_is_actionable_and_html_is_still_available(report_client):
    client, _, _, _ = report_client

    def fail(_path):
        raise PdfRenderError("服务器未安装 PDF 渲染依赖")

    client.app.state.runtime.pdf_service.render = fail
    response = client.get('/api/v1/reports/saved/download?format=pdf&device_id=Cutter01')
    assert response.status_code == 503
    assert "渲染依赖" in response.json()["detail"]
    assert client.get('/api/v1/reports/saved/download?device_id=Cutter01').status_code == 200


def test_missing_report_and_invalid_format_do_not_render(report_client):
    client, _, calls, _ = report_client
    assert client.get('/api/v1/reports/missing/download?format=pdf&device_id=Cutter01').status_code == 404
    assert client.get('/api/v1/reports/saved/download?format=exe&device_id=Cutter01').status_code == 422
    assert calls == []


def test_pdf_cannot_resolve_a_report_outside_the_report_directory(report_client, tmp_path):
    client, job, calls, _ = report_client
    (tmp_path / "outside.html").write_text("secret")
    job["relative_path"] = "../outside.html"
    assert client.get('/api/v1/reports/saved/download?format=pdf&device_id=Cutter01').status_code == 404
    assert calls == []


def test_completed_record_with_a_missing_archive_is_unavailable_in_status_and_history(report_client):
    client, job, calls, html = report_client
    job.update(report_id="saved", device_id="Cutter01", stage_message="报告生成成功", report_date="2026-08-18",
               project_code=None, ship_no=None, created_at="2026-08-18T09:00:00+08:00", file_size=html.stat().st_size)
    from app.modules.reports import routes as reports

    assert reports._serialize_job(job, html.parent)["status"] == 'completed'
    html.unlink()
    item = reports._serialize_job(job, html.parent)
    assert item['status'] == 'failed' and item['error']['code'] == 'REPORT_FILE_UNAVAILABLE'
    assert 'download_url' not in item and 'view_url' not in item
    assert client.get('/api/v1/reports/saved/download?device_id=Cutter01').status_code == 404
    assert job['status'] == 'completed'
    assert calls == []


def test_pdf_cache_matches_html_content_even_when_timestamps_match(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("<p>报表数值 111</p>")
    stamp = html.stat()
    service = PdfService(operation_timeout_seconds=30)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(service.render, (html, html)))
    assert first == second
    first_pdf = first.read_bytes()
    inode = first.stat().st_ino
    assert service.render(html).stat().st_ino == inode
    html.write_text("<p>报表数值 222</p>")
    os.utime(html, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert service.render(html).read_bytes() != first_pdf
    assert service.render(html).stat().st_ino != inode


def test_incomplete_pdf_and_missing_chart_are_never_published(tmp_path, monkeypatch):
    html = tmp_path / "report.html"
    html.write_text('<p>有效报表</p><div class="chart" id="missing"></div>')
    with pytest.raises(PdfRenderError, match="图表"):
        PdfService().render(html)
    renderer = PdfService()
    monkeypatch.setattr(renderer, "_render_with_playwright", lambda _, output: output.write_bytes(b"%PDF-1.7\npartial"))
    with pytest.raises(PdfRenderError, match="完整文件"):
        renderer.render(html)
    assert not list(tmp_path.glob('.report-*'))
    assert not html.with_suffix('.pdf').exists()


def test_pdf_uses_one_continuous_page_without_chapter_breaks_or_page_numbers(tmp_path):
    import pypdfium2 as pdfium
    source = tmp_path / 'continuous.html'
    source.write_text('<style>@page{size:A4}h2{break-before:page}</style>'
                      + ''.join(f'<h2>章节{index}</h2><p>完整内容</p>' for index in range(30))
                      + '<p>末尾校验标记</p>')
    pdf = PdfService(operation_timeout_seconds=30).render(source)
    with pdfium.PdfDocument(pdf) as document:
        assert len(document) == 1
        page = document[0]
        assert page.get_height() > 842
        text = page.get_textpage().get_text_range()
        assert '章节0' in text and '章节29' in text and '末尾校验标记' in text
