from dataclasses import replace
from datetime import datetime, timedelta
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.auth import optional_user
from app.api.downloads import router
from app.config import settings
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository, SqlServerPdmRepository
from app.services.file_download_service import DownloadError, FileDownloadService, download_filename
from app.services.file_source import HttpFileSource, FileSourceError, normalize_relative_path
from app.workers.download_cleanup_worker import DownloadCleanupWorker


ADMIN = {"user_id": "test-admin", "display_name": "管理员", "role": "admin"}


class Response(BytesIO):
    def __init__(self, content=b"GEN DATA\n", headers=None, status=200):
        super().__init__(content)
        self.headers = headers if headers is not None else {"Content-Type": "application/octet-stream", "Content-Length": str(len(content))}
        self.status = status


class Opener:
    def __init__(self, content=b"GEN DATA\n", headers=None, status=200, error=None):
        self.content, self.headers, self.status, self.error = content, headers, status, error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return Response(self.content, self.headers, self.status)


def make_service(tmp_path, source=None):
    cfg = replace(settings, project_root=tmp_path, file_download_max_bytes=1024, file_http_authorization="", file_http_cookie="",
                  file_http_base_url="http://files.internal:8800")
    repository = DownloadRepository(tmp_path / "metadata.db")
    repository.initialize()
    pdm = MockPdmRepository()
    pdm.mode = "sqlserver"
    source = source or HttpFileSource(cfg, opener=Opener())
    service = FileDownloadService(cfg, repository, pdm, source)
    raw = pdm.get_file("F1A0796D-7118-4E37-8F69-B6D917BD6456")
    payload = {"document_id": raw["document_id"], "version_id": raw["version_id"], "file_path": raw["file_path"]}
    return service, payload


def test_http_query_encodes_path_and_has_separate_optional_auth(tmp_path):
    cfg = replace(settings, file_http_base_url="http://files.internal:8800", file_http_authorization="Bearer file-only", file_http_cookie="file-session=test")
    opener = Opener()
    HttpFileSource(cfg, opener=opener).download(r"Root\2026-08-25\中文 空格.gen", tmp_path / "download.part")
    request = opener.requests[0]
    assert parse_qs(urlsplit(request.full_url).query) == {"filepath": [r"Root\2026-08-25\中文 空格.gen"], "contentType": ["application/octet-stream"]}
    assert request.headers["Authorization"] == "Bearer file-only"
    assert request.headers["Cookie"] == "file-session=test"
    assert "file-only" not in repr(cfg)


@pytest.mark.parametrize("path", ["../x.gen", r"D:\secret.gen", "%2e%2e/secret.gen", "Root/a\r\n.gen", "Root/a.gen:secret", "Root/.. /x.gen"])
def test_source_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        normalize_relative_path(path)


@pytest.mark.parametrize("base", ["file:///etc", "http://user:pass@files", "http://files?secret=x", "http://files/path", "http://files/#fragment"])
def test_http_configuration_rejects_unsafe_endpoint(base):
    cfg = replace(settings, file_http_base_url=base)
    assert HttpFileSource(cfg).status()["enabled"] is False


@pytest.mark.parametrize("content,headers,status", [
    (b"", {"Content-Type": "application/octet-stream", "Content-Length": "0"}, 200),
    (b"x", {"Content-Type": "application/json"}, 200),
    (b"x", {"Content-Type": "text/html"}, 200),
    (b"x", {"Content-Type": "application/octet-stream", "Content-Length": "100"}, 200),
    (b"x" * 1025, {"Content-Type": "application/octet-stream"}, 200),
    (b"x", {"Content-Type": "application/octet-stream", "Content-Length": "2048"}, 200),
    (b"x", {"Content-Type": "application/octet-stream", "Content-Encoding": "gzip"}, 200),
    (b"partial", {"Content-Type": "application/octet-stream"}, 206),
    (b'{"IsSuccess":false,"Message":"bad"}', {"Content-Type": "application/octet-stream"}, 200),
    (b"<html>login</html>", {"Content-Type": "application/octet-stream"}, 200),
])
def test_failed_download_not_published_and_partial_is_cleaned(tmp_path, content, headers, status):
    service, payload = make_service(tmp_path)
    service.source.opener = Opener(content, headers, status)
    with pytest.raises(DownloadError):
        service.download(**payload, user=ADMIN)
    record = service.list()["items"][0]
    assert record["status"] == "failed" and not record["available"]
    assert not list(service.settings.cutting_downloads_dir.rglob("*.part"))
    with pytest.raises(DownloadError):
        service.local_file(record["download_id"])


@pytest.mark.parametrize("code", [301, 302, 401, 403, 404, 500])
def test_http_errors_hide_credentials_and_remote_body(tmp_path, code):
    service, payload = make_service(tmp_path)
    service.source.opener = Opener(error=HTTPError("http://files/?secret=do-not-log", code, "remote-secret", {}, BytesIO(b"private body")))
    with pytest.raises(DownloadError) as error:
        service.download(**payload, user=ADMIN)
    assert "secret" not in str(error.value)
    assert "private" not in str(service.list())


def test_connection_failure_safe_message(tmp_path):
    service, payload = make_service(tmp_path)
    service.source.opener = Opener(error=URLError("secret in remote URL"))
    with pytest.raises(DownloadError) as error:
        service.download(**payload, user=ADMIN)
    assert "secret" not in str(error.value)


def test_names_and_separate_downloads_persist_without_overwrite(tmp_path):
    service, payload = make_service(tmp_path)
    first = service.download(**payload, user=ADMIN)
    second = service.download(**payload, user=ADMIN)
    assert first["filename"] == "f7e50f62-d508-430e-8309-8bf309568095.GEN"
    assert "版本-" not in first["filename"]
    assert first["filename"] == second["filename"]
    assert first["download_id"] != second["download_id"]
    assert first["sha256"] == second["sha256"] and first["size"] == 9
    assert first["project_name"] == "扬州切割联调项目"
    assert first["ship_no"] == "CMYZ0177"
    assert first["pbs_info"] == "PK-707C1"
    assert first["catalog_id"] == "CAT-GEN"
    assert [node["title"] for node in first["catalog_path"]] == ["船体专业", "PK-707C1", "GEN切割文件"]
    record, local = service.local_file(first["download_id"])
    assert local.is_relative_to(service.settings.cutting_downloads_dir)
    assert local.read_bytes() == b"GEN DATA\n"
    assert DownloadRepository(service.repository.database).list(1, 10)[1] == 2
    service.source.enabled = False
    assert service.local_file(first["download_id"])[1].exists()


def test_pushed_copy_is_deleted_and_the_same_version_can_be_downloaded_again(tmp_path):
    service, payload = make_service(tmp_path)
    first = service.download(**payload, user=ADMIN)
    _record, first_path = service.local_file(first["download_id"])

    cleanup = service.delete_after_push(
        first["download_id"],
        dispatch_id="dispatch-test-1",
    )

    assert cleanup == {"deleted": True, "already_deleted": False, "error": None}
    assert not first_path.exists()
    stored = service.repository.get(first["download_id"])
    assert stored["cleanup_reason"] == "pushed"
    assert stored["cleanup_dispatch_id"] == "dispatch-test-1"
    assert service.public(stored)["status_label"] == "已推送清理"
    with pytest.raises(DownloadError, match="推送成功后删除"):
        service.local_file(first["download_id"])

    repeated = service.download(**payload, user=ADMIN)
    assert repeated["download_id"] != first["download_id"]
    assert service.local_file(repeated["download_id"])[1].is_file()


def test_filename_sanitizes_windows_names_and_limits_utf8():
    assert download_filename("板件A.GEN", "A.2", "Root/a.gen") == "板件A.GEN"
    assert download_filename("CMYZ0136-PEBC231S-AT01", "V", "Root/source.pdf") == "CMYZ0136-PEBC231S-AT01.pdf"
    assert download_filename("CON", "1", "Root/a.gen").startswith("_CON")
    name = download_filename("文件" * 100 + ".gen", "版本" * 100, "Root/a.gen")
    assert len(name.encode()) <= 240
    name = download_filename('../x\\bad\r\n".gen', "A/B", "Root/a.gen")
    assert all(char not in name for char in '/\\\r\n"')


@pytest.mark.parametrize("changed", ["version", "path", "multiple"])
def test_stale_or_ambiguous_selection_does_not_fetch(tmp_path, changed, monkeypatch):
    service, payload = make_service(tmp_path)
    raw = dict(service.pdm.get_file(payload["document_id"]))
    if changed == "version":
        raw["version_id"] = "new-version"
    elif changed == "path":
        raw["file_path"] = "Root/new.gen"
    else:
        raw["physical_file_count"] = 2
    monkeypatch.setattr(service.pdm, "get_file", lambda _: raw)
    with pytest.raises(DownloadError) as error:
        service.download(**payload, user=ADMIN)
    assert error.value.status_code == 409
    assert not service.source.opener.requests


def test_real_service_cannot_download_mock_pdm_paths(tmp_path):
    service, payload = make_service(tmp_path)
    service.pdm.mode = "mock"
    with pytest.raises(DownloadError):
        service.download(**payload, user=ADMIN)
    assert not service.source.opener.requests


def test_strict_version_mapping_query():
    assert "f.VersionID = a.VersionID OR (f.VersionID IS NULL AND a.VersionID IS NULL)" in SqlServerPdmRepository.FILE_FROM
    assert "COUNT(*) OVER ()" in SqlServerPdmRepository.FILE_FROM
    assert "CASE WHEN f.VersionID" not in SqlServerPdmRepository.FILE_FROM
    assert "f.FileName" in SqlServerPdmRepository.FILE_FROM
    assert "f.VersionCode" in SqlServerPdmRepository.FILE_FROM
    assert "c.ClassificationType AS classification_type" in SqlServerPdmRepository.FILE_COLUMNS


def test_missing_or_modified_local_file_not_served(tmp_path):
    service, payload = make_service(tmp_path)
    result = service.download(**payload, user=ADMIN)
    _, path = service.local_file(result["download_id"])
    path.write_bytes(b"EVIL DATA")
    with pytest.raises(DownloadError, match="校验失败"):
        service.local_file(result["download_id"])
    path.unlink()
    assert service.list()["items"][0]["available"] is False


def test_expired_download_file_is_deleted_but_audit_record_is_retained(tmp_path):
    service, payload = make_service(tmp_path)
    result = service.download(**payload, user=ADMIN)
    item, path = service.local_file(result["download_id"])
    completed_at = datetime.fromisoformat(item["downloaded_at"])
    old = (completed_at - timedelta(days=8)).isoformat(timespec="seconds")
    item.update(downloaded_at=old, updated_at=old)
    service.repository.save(item)

    cleanup = service.cleanup_expired(now=completed_at)

    assert cleanup == {"cleaned": 1, "missing": 0, "failed": 0}
    assert not path.exists()
    stored = service.repository.get(result["download_id"])
    assert stored["status"] == "downloaded"
    assert stored["cleanup_reason"] == "retention_expired"
    public = service.list()["items"][0]
    assert public["status_label"] == "已自动清理"
    assert public["available"] is False
    with pytest.raises(DownloadError, match="保留期"):
        service.local_file(result["download_id"])
    assert service.cleanup_expired(now=completed_at) == {"cleaned": 0, "missing": 0, "failed": 0}


def test_recent_download_is_not_cleaned(tmp_path):
    service, payload = make_service(tmp_path)
    result = service.download(**payload, user=ADMIN)
    item, path = service.local_file(result["download_id"])
    completed_at = datetime.fromisoformat(item["downloaded_at"])

    assert service.cleanup_expired(now=completed_at + timedelta(days=6, hours=23)) == {
        "cleaned": 0,
        "missing": 0,
        "failed": 0,
    }
    assert path.is_file()


def test_cleanup_never_follows_a_symlink_outside_download_root(tmp_path):
    service, payload = make_service(tmp_path)
    result = service.download(**payload, user=ADMIN)
    item, path = service.local_file(result["download_id"])
    completed_at = datetime.fromisoformat(item["downloaded_at"])
    old = (completed_at - timedelta(days=8)).isoformat(timespec="seconds")
    item.update(downloaded_at=old, updated_at=old)
    service.repository.save(item)
    external = tmp_path / "must-stay.gen"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)

    assert service.cleanup_expired(now=completed_at) == {
        "cleaned": 0,
        "missing": 0,
        "failed": 1,
    }
    assert external.read_bytes() == b"GEN DATA\n"
    assert not service.repository.get(result["download_id"]).get("file_deleted_at")


def test_cleanup_worker_runs_immediately_and_stops_cleanly():
    completed = threading.Event()

    class Service:
        calls = 0

        def cleanup_expired(self):
            self.calls += 1
            completed.set()
            return {"cleaned": 0, "missing": 0, "failed": 0}

    service = Service()
    worker = DownloadCleanupWorker(
        replace(settings, file_download_retention_days=7),
        service,
    )
    worker.start()
    assert completed.wait(1)
    worker.stop()
    assert service.calls == 1
    assert not worker.thread.is_alive()


def test_symlink_cannot_escape_download_root(tmp_path):
    service, payload = make_service(tmp_path)
    result = service.download(**payload, user=ADMIN)
    _, path = service.local_file(result["download_id"])
    external = tmp_path / "external.gen"
    external.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(DownloadError, match="路径校验失败"):
        service.local_file(result["download_id"])


def test_restart_marks_only_incomplete_records_failed(tmp_path):
    service, payload = make_service(tmp_path)
    finished = service.download(**payload, user=ADMIN)
    pending = dict(finished, download_id="dl_" + "a" * 32, status="downloading")
    service.repository.save(pending)
    service.repository.recover_interrupted(service.now())
    assert service.repository.get(pending["download_id"])["status"] == "failed"
    assert service.repository.get(finished["download_id"])["status"] == "downloaded"


@pytest.mark.parametrize("user,expected", [(None, 401), ({"role": "worker"}, 403)])
def test_download_routes_are_admin_only(tmp_path, user, expected):
    service, payload = make_service(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.file_downloads = service
    app.dependency_overrides[optional_user] = lambda: user
    client = TestClient(app)
    assert client.post("/api/v1/cutting/downloads", json=payload).status_code == expected
    assert client.get("/api/v1/cutting/downloads").status_code == expected
    assert service.repository.list(1, 10)[1] == 0


def test_api_download_saves_record_without_exposing_file_content(tmp_path):
    service, payload = make_service(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.file_downloads = service
    app.dependency_overrides[optional_user] = lambda: ADMIN

    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test-request"
        return await call_next(request)

    client = TestClient(app)
    response = client.post("/api/v1/cutting/downloads", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    listing = client.get("/api/v1/cutting/downloads").json()
    assert "GEN DATA" not in str(listing)
    assert client.get(f"/api/v1/cutting/downloads/{data['download_id']}/file").status_code == 404
    assert client.get(f"/api/v1/cutting/downloads/{data['download_id']}/preview").status_code == 404
    assert client.post("/api/v1/cutting/downloads", json={**payload, "url": "http://untrusted/"}).status_code == 422


def test_real_http_transport_get_and_redirect_not_followed(tmp_path):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            parameters = parse_qs(urlsplit(self.path).query)
            if parameters.get("filepath") == [r"Root\redirect.gen"]:
                self.send_response(302)
                self.send_header("Location", "/must-not-follow")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"GEN DATA\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cfg = replace(settings, file_http_base_url=f"http://127.0.0.1:{server.server_port}", file_http_authorization="", file_http_cookie="")
        source = HttpFileSource(cfg)
        target = tmp_path / "actual.gen"
        source.download(r"Root\中文.gen", target)
        assert target.read_bytes() == b"GEN DATA\n"
        with pytest.raises(FileSourceError, match="302"):
            source.download(r"Root\redirect.gen", tmp_path / "redirect.gen")
        assert len(requests) == 2
        assert all(urlsplit(path).path == "/FOWebApi/api/FO/FilePreView" for path in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_concurrency_limit_does_not_create_or_fetch_file(tmp_path):
    service, payload = make_service(tmp_path)
    service._slots.acquire()
    service._slots.acquire()
    try:
        with pytest.raises(DownloadError) as error:
            service.download(**payload, user=ADMIN)
        assert error.value.status_code == 429
        assert not service.source.opener.requests
        assert service.list()["total"] == 0
    finally:
        service._slots.release()
        service._slots.release()
