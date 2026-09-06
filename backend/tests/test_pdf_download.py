from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import reports
from app.api.auth import require_admin
from app.services.pdf_service import PdfRenderError, PdfService


@pytest.fixture
def report_client(tmp_path, monkeypatch):
    html = tmp_path / "saved.html"
    html.write_text("<h1>车间日报</h1>", encoding="utf-8")
    job = {"relative_path": html.name, "filename": "车间日报_2026-08-18.html"}
    calls = []

    def render(path):
        calls.append(path)
        pdf = path.with_suffix(".pdf")
        pdf.write_bytes(b"%PDF-1.7\nreport content")
        return pdf

    app = FastAPI()
    app.include_router(reports.router)
    app.dependency_overrides[require_admin] = lambda: {"role": "admin"}
    app.state.metadata = SimpleNamespace(get_job=lambda report_id: job if report_id == "saved" else None)
    app.state.pdf_service = SimpleNamespace(render=render)
    monkeypatch.setattr(reports, "settings", replace(reports.settings, project_root=tmp_path))
    # Settings resolves report files under project_root/reports.
    (tmp_path / "reports").mkdir()
    html = html.rename(tmp_path / "reports" / html.name)
    return TestClient(app), job, calls, html


def test_html_download_stays_unchanged_and_does_not_render_pdf(report_client):
    client, _, calls, html = report_client
    response = client.get("/api/v1/reports/saved/download")
    assert response.status_code == 200
    assert response.content == html.read_bytes()
    assert response.headers["content-type"].startswith("text/html")
    assert calls == []


def test_pdf_download_has_pdf_bytes_type_and_chinese_filename(report_client):
    client, _, calls, html = report_client
    response = client.get("/api/v1/reports/saved/download?format=pdf")
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

    client.app.state.pdf_service.render = fail
    response = client.get("/api/v1/reports/saved/download?format=pdf")
    assert response.status_code == 503
    assert "渲染依赖" in response.json()["detail"]
    assert client.get("/api/v1/reports/saved/download").status_code == 200


def test_missing_report_and_invalid_format_do_not_render(report_client):
    client, _, calls, _ = report_client
    assert client.get("/api/v1/reports/missing/download?format=pdf").status_code == 404
    assert client.get("/api/v1/reports/saved/download?format=exe").status_code == 422
    assert calls == []


def test_pdf_cannot_resolve_a_report_outside_the_report_directory(report_client, tmp_path):
    client, job, calls, _ = report_client
    (tmp_path / "outside.html").write_text("secret")
    job["relative_path"] = "../outside.html"
    assert client.get("/api/v1/reports/saved/download?format=pdf").status_code == 404
    assert calls == []


def test_cached_pdf_does_not_need_to_launch_a_browser(tmp_path):
    html = tmp_path / "report.html"
    html.write_text("report")
    pdf = html.with_suffix(".pdf")
    pdf.write_bytes(b"%PDF-1.7\nexisting report")
    assert PdfService().render(html) == pdf
