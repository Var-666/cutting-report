from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
import unicodedata
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.services.file_source import FileSourceError, normalize_relative_path


class DownloadError(RuntimeError):
    def __init__(self, message, status_code=422, download_id=None):
        super().__init__(message)
        self.status_code = status_code
        self.download_id = download_id


def safe_component(value, limit=65):
    cleaned = "".join(char for char in str(value) if unicodedata.category(char) not in {"Cc", "Cf"})
    cleaned = re.sub(r'[<>:"/\\|?*]', "_", cleaned).strip(" .")[:limit].rstrip(" .")
    while len(cleaned.encode("utf-8")) > limit * 2:
        cleaned = cleaned[:-1]
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", cleaned, re.I):
        cleaned = "_" + cleaned
    return cleaned or "未命名"


def download_filename(document_no, version, relative_path):
    original = PurePosixPath(normalize_relative_path(relative_path)).name
    extension = PurePosixPath(original).suffix
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", extension):
        extension = ".bin"
    # PDM DocumentNo is the user-facing file name. Keep it unchanged except for
    # Windows-safe normalization; only recover the physical file extension when
    # DocumentNo itself has none. Versions remain metadata, not part of the name.
    title = safe_component(document_no or original, 110)
    if re.fullmatch(r"\.[A-Za-z0-9]{1,12}", PurePosixPath(title).suffix):
        return title
    return f"{title}{extension}"


class FileDownloadService:
    def __init__(self, settings, repository, pdm, source):
        self.settings, self.repository, self.pdm, self.source = settings, repository, pdm, source
        self._slots = threading.BoundedSemaphore(2)

    def now(self):
        return datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds")

    def _path(self, item):
        root = self.settings.cutting_downloads_dir.resolve()
        path = root / item["download_id"] / item["filename"]
        if (not re.fullmatch(r"dl_[a-f0-9]{32}", item["download_id"])
                or Path(item["filename"]).name != item["filename"]
                or "\\" in item["filename"]
                or not path.resolve().is_relative_to(root)):
            raise DownloadError("本地文件路径校验失败", 409)
        return path

    def public(self, item):
        result = dict(item)
        result["status_label"] = {"downloading": "下载中", "downloaded": "已下载", "failed": "下载失败"}[item["status"]]
        result["available"] = item["status"] == "downloaded" and self._path(item).is_file()
        result["storage_path"] = f"data/cutting-downloads/{item['download_id']}/{item['filename']}"
        if item["status"] == "downloaded" and not result["available"]:
            result["status_label"] = "本地文件丢失"
            result["error"] = "记录仍在，但本地文件已不存在，请重新下载"
        return result

    def list(self, page=1, page_size=10, filters=None, sort=()):
        items, total = self.repository.list(page, page_size, filters, sort=sort)
        return {"items": [self.public(item) for item in items], "total": total, "page": page, "page_size": page_size}

    def download(self, *, document_id, version_id, file_path, user):
        raw = self.pdm.get_file(document_id)
        if raw is None:
            raise DownloadError("未找到对应文档", 404)
        if str(raw.get("version_id") or "") != str(version_id or ""):
            raise DownloadError("文档版本已变化，请刷新文件清单后重新选择", 409)
        if int(raw.get("physical_file_count") or 0) > 1:
            raise DownloadError("该版本对应多个文件，不能确定下载目标，请核对文件关联", 409)
        try:
            actual = normalize_relative_path(str(raw.get("file_path") or ""))
            expected = normalize_relative_path(file_path)
        except ValueError as exc:
            raise DownloadError(str(exc)) from None
        if actual != expected:
            raise DownloadError("文档文件路径已变化，请刷新文件清单后重新选择", 409)
        if self.source.mode == "http" and self.pdm.mode != "sqlserver":
            raise DownloadError("请先启用真实 PDM 数据库，不能用模拟清单请求真实文件服务")
        if not self.source.status()["enabled"]:
            raise DownloadError("文件来源未启用或配置无效，请先配置下载服务", 503)
        if not self._slots.acquire(blocking=False):
            raise DownloadError("当前已有两个下载进行中，请稍后重试", 429)
        partial = None
        completed = None
        recorded = False
        try:
            version = raw.get("version") or raw.get("physical_version_no")
            item = {
                "download_id": "dl_" + uuid4().hex, "document_id": str(raw["document_id"]),
                "version_id": str(raw.get("version_id") or ""), "version": str(version or "未标注"),
                "document_no": str(raw.get("document_no") or ""), "relative_path": actual,
                "project_id": str(raw.get("project_id") or ""),
                "filename": download_filename(raw.get("document_no"), version, actual),
                "source_mode": self.source.mode, "is_simulation": self.pdm.mode == "mock" or self.source.mode == "mock",
                "created_by_name": user["display_name"], "created_by_id": user["user_id"],
                "created_at": self.now(), "updated_at": self.now(), "status": "downloading",
                "size": None, "sha256": None, "error": None,
            }
            self.repository.save(item)
            recorded = True
            target = self._path(item)
            target.parent.mkdir(parents=True, exist_ok=False)
            partial = target.with_name(target.name + ".part")
            self.source.download(actual, partial)
            size = partial.stat().st_size
            if not 0 < size <= self.settings.file_download_max_bytes:
                raise FileSourceError("文件为空或超过允许的下载大小")
            with partial.open("rb") as stream:
                first = stream.read(min(size, 65536)).lstrip(b"\xef\xbb\xbf \t\r\n")
                if self.source.mode == "http":
                    if first.lower().startswith((b"<!doctype html", b"<html")):
                        raise FileSourceError("文件服务返回了 HTML 页面，不是切割文件")
                    if size <= 65536 and first.startswith(b"{"):
                        try:
                            body = json.loads(first)
                        except (ValueError, UnicodeError):
                            body = None
                        if isinstance(body, dict) and (body.get("IsSuccess") is False or "error" in body or "Message" in body):
                            raise FileSourceError("文件服务返回了错误信息，未保存为有效文件")
                stream.seek(0)
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            # Each attempt has its own UUID directory. Existing versions are never overwritten.
            partial.replace(target)
            completed = target
            item.update(status="downloaded", size=size, sha256=digest, updated_at=self.now())
            self.repository.save(item)
            return self.public(item)
        except (FileSourceError, OSError) as exc:
            message = str(exc) if isinstance(exc, FileSourceError) else "本地文件保存失败，请检查可用空间与目录权限"
            if recorded:
                item.update(status="failed", error=message, updated_at=self.now())
                self.repository.save(item)
            raise DownloadError(message, 502, item["download_id"] if recorded else None) from None
        finally:
            try:
                if partial is not None:
                    partial.unlink(missing_ok=True)
                # Retain a completed file if metadata persistence fails, for recovery.
                if partial is not None and completed is None:
                    partial.parent.rmdir()
            except OSError:
                pass
            finally:
                self._slots.release()

    def get_or_download(self, *, document_id, version_id, file_path, user):
        """Reuse a verified exact-version file, otherwise create a new download."""
        try:
            normalized = normalize_relative_path(file_path)
        except ValueError as exc:
            raise DownloadError(str(exc)) from None
        existing = self.repository.find_latest(document_id, version_id, normalized)
        if existing is not None:
            try:
                item, path = self.local_file(existing["download_id"])
                return self.public(item), path
            except DownloadError:
                # Keep history immutable and replace an invalid local copy with a
                # new UUID download instead of silently uploading stale bytes.
                pass
        item = self.download(
            document_id=document_id,
            version_id=version_id,
            file_path=normalized,
            user=user,
        )
        _stored, path = self.local_file(item["download_id"])
        return item, path

    def local_file(self, download_id):
        item = self.repository.get(download_id)
        if item is None:
            raise DownloadError("下载记录不存在", 404)
        if item["status"] != "downloaded":
            raise DownloadError("该文件尚未成功下载", 409)
        path = self._path(item)
        try:
            if path.stat().st_size != item["size"]:
                raise DownloadError("本地文件大小发生变化，请重新下载", 409)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != item["sha256"]:
                raise DownloadError("本地文件内容校验失败，请重新下载", 409)
        except OSError:
            raise DownloadError("本地文件不存在或无法读取，请重新下载", 404) from None
        return item, path
