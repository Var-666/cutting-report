from __future__ import annotations

import shutil
import time
from http.client import HTTPException
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from app.config import Settings


class FileSourceError(RuntimeError):
    pass


class FileSourceNotConfiguredError(FileSourceError):
    pass


def normalize_relative_path(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    if not cleaned or any(ord(char) < 32 for char in cleaned) or "%" in cleaned:
        raise ValueError("文件相对路径为空或包含非法字符")
    windows_path = PureWindowsPath(cleaned)
    posix_path = PurePosixPath(cleaned)
    if windows_path.drive or windows_path.is_absolute() or posix_path.is_absolute():
        raise ValueError("文件路径必须是相对于文件服务器根目录的路径")
    parts = [part for part in posix_path.parts if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("文件路径不能跳出文件服务器根目录")
    if any(":" in part or part.endswith((" ", ".")) for part in parts):
        raise ValueError("文件路径包含不支持的路径片段")
    return "/".join(parts)


class FileSource(Protocol):
    mode: str
    enabled: bool

    def status(self) -> dict: ...

    def exists(self, relative_path: str) -> bool: ...

    def download(self, relative_path: str, target: Path) -> Path: ...


class DisabledFileSource:
    mode = "disabled"
    enabled = False

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "enabled": False,
            "label": "文件下载未配置",
            "message": "请配置 FILE_SOURCE_MODE=http 接入文件下载服务；系统已保存文件仍会保留。",
        }

    def exists(self, relative_path: str) -> bool:
        normalize_relative_path(relative_path)
        return False

    def download(self, relative_path: str, target: Path) -> Path:
        normalize_relative_path(relative_path)
        raise FileSourceNotConfiguredError("文件下载服务尚未配置")


class MockFileSource:
    mode = "mock"
    enabled = True

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "enabled": True,
            "label": "模拟文件来源",
            "message": "只生成模拟文件，不连接内网文件服务器。",
        }

    def exists(self, relative_path: str) -> bool:
        normalize_relative_path(relative_path)
        return True

    def download(self, relative_path: str, target: Path) -> Path:
        normalized = normalize_relative_path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"MOCK CUTTING FILE\n{normalized}\n".encode("utf-8"))
        return target


class LocalFileSource:
    mode = "local"
    enabled = True

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "enabled": self.root.is_dir(),
            "label": "本地只读目录",
            "message": "仅用于部署前的本地文件联调。",
        }

    def resolve(self, relative_path: str) -> Path:
        normalized = normalize_relative_path(relative_path)
        candidate = self.root.joinpath(*PurePosixPath(normalized).parts).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("文件路径不能跳出文件服务器根目录") from exc
        return candidate

    def exists(self, relative_path: str) -> bool:
        return self.resolve(relative_path).is_file()

    def download(self, relative_path: str, target: Path) -> Path:
        source = self.resolve(relative_path)
        if not source.is_file():
            raise FileSourceError("文件服务器中未找到指定文件")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return target


class _NoFileRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpFileSource:
    """Read only the configured FO endpoint, with bounded downloads and no redirects."""

    mode = "http"
    enabled = True

    def __init__(self, settings: Settings, *, opener=None):
        self.settings = settings
        self.opener = opener or build_opener(ProxyHandler({}), _NoFileRedirect())

    def _endpoint(self):
        base = self.settings.file_http_base_url.rstrip("/")
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment or parsed.path):
            raise FileSourceNotConfiguredError("文件接口地址无效，应填写 http(s)://服务器:端口")
        for value in (self.settings.file_http_authorization, self.settings.file_http_cookie):
            if any(ord(char) < 32 for char in value):
                raise FileSourceNotConfiguredError("文件接口认证配置包含非法字符")
        if not 1 <= self.settings.file_download_timeout <= 300 or not 1 <= self.settings.file_download_max_bytes <= 1024 ** 3:
            raise FileSourceNotConfiguredError("文件下载超时或大小限制配置无效")
        return base + "/FOWebApi/api/FO/FilePreView"

    def status(self):
        try:
            self._endpoint()
            return {"mode": self.mode, "enabled": True, "label": "FO HTTP 文件服务",
            "message": "已配置 HTTP 下载；连通性与权限以实际保存结果为准。文件保存在系统服务器中。"}
        except FileSourceError as exc:
            return {"mode": self.mode, "enabled": False, "label": "文件接口配置无效", "message": str(exc)}

    def exists(self, relative_path: str) -> bool:
        normalize_relative_path(relative_path)
        raise FileSourceError("该接口未提供独立存在性检查，请通过下载结果确认")

    def download(self, relative_path: str, target: Path) -> Path:
        normalized = normalize_relative_path(relative_path)
        url = self._endpoint() + "?" + urlencode({
            "filepath": normalized.replace("/", "\\"), "contentType": "application/octet-stream",
        })
        headers = {"Accept": "application/octet-stream", "Accept-Encoding": "identity"}
        if self.settings.file_http_authorization:
            headers["Authorization"] = self.settings.file_http_authorization
        if self.settings.file_http_cookie:
            headers["Cookie"] = self.settings.file_http_cookie
        deadline = time.monotonic() + self.settings.file_download_timeout
        try:
            with self.opener.open(Request(url, headers=headers), timeout=self.settings.file_download_timeout) as response:
                if response.status != 200:
                    raise FileSourceError(f"文件服务返回 HTTP {response.status}，未保存文件")
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if mime != "application/octet-stream":
                    raise FileSourceError("文件服务未返回约定的二进制流，可能是登录页或错误消息")
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise FileSourceError("文件服务返回了不支持的压缩传输格式")
                raw_length = response.headers.get("Content-Length")
                expected = int(raw_length) if raw_length is not None else None
                if expected is not None and (expected <= 0 or expected > self.settings.file_download_max_bytes):
                    raise FileSourceError("文件为空或超过允许的下载大小")
                target.parent.mkdir(parents=True, exist_ok=True)
                size = 0
                with target.open("xb") as stream:
                    while True:
                        if time.monotonic() > deadline:
                            raise FileSourceError("文件下载超时，请重试")
                        chunk = response.read(65536)
                        if time.monotonic() > deadline:
                            raise FileSourceError("文件下载超时，请重试")
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > self.settings.file_download_max_bytes:
                            raise FileSourceError("文件超过允许的下载大小")
                        stream.write(chunk)
                if not size or (expected is not None and size != expected):
                    raise FileSourceError("文件为空或传输不完整，请重新下载")
        except HTTPError as exc:
            code = exc.code
            exc.close()
            if code in {401, 403}:
                raise FileSourceError("文件服务认证失败，请核对该服务的认证配置（不是能管账号）") from None
            if code == 404:
                raise FileSourceError("文件服务中未找到对应路径") from None
            raise FileSourceError(f"文件服务返回 HTTP {code}，未保存文件") from None
        except (URLError, OSError, HTTPException, ValueError):
            # Do not forward remote URLs, credentials, cookies or response bodies.
            raise FileSourceError("文件下载失败，请检查内网连通性、传输完整性和本地可用空间") from None
        return target


def build_file_source(settings: Settings) -> FileSource:
    if settings.file_source_mode == "http":
        return HttpFileSource(settings)
    if settings.file_source_mode == "mock":
        return MockFileSource()
    if settings.file_source_mode == "local" and settings.file_source_local_root:
        return LocalFileSource(Path(settings.file_source_local_root))
    return DisabledFileSource()
