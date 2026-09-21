from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.retention import RetentionRepository


logger = logging.getLogger(__name__)


class RetentionService:
    def __init__(self, settings: Settings, repository: RetentionRepository) -> None:
        self.settings = settings
        self.repository = repository
        self._cleanup_lock = threading.Lock()

    def has_pending_file_deletions(self) -> bool:
        return self.repository.has_pending_file_deletions()

    @staticmethod
    def _cutoff(reference: datetime, days: int) -> str | None:
        if days <= 0:
            return None
        return (reference - timedelta(days=days)).isoformat(timespec="seconds")

    def _delete_report_file(self, relative_path: str) -> bool:
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("报告清理路径无效")
        root = self.settings.reports_dir.resolve()
        candidate = root / Path(*relative.parts)
        parent = candidate.parent.resolve()
        if not parent.is_relative_to(root):
            raise ValueError("报告清理路径越界")
        if candidate.is_symlink():
            existed = True
            candidate.unlink()
        else:
            path = candidate.resolve()
            if not path.is_relative_to(root):
                raise ValueError("报告清理路径越界")
            existed = path.is_file()
            path.unlink(missing_ok=True)
        while parent != root and parent.is_relative_to(root):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return existed

    def cleanup_expired(self, now: datetime | None = None) -> dict[str, Any]:
        if not self._cleanup_lock.acquire(blocking=False):
            return {"skipped": True, "records": {}, "files": {}}
        try:
            reference = now or datetime.now(ZoneInfo(self.settings.timezone))
            current = reference.isoformat(timespec="seconds")
            batch_size = min(
                max(1, self.settings.retention_cleanup_batch_size),
                500,
            )
            records = self.repository.cleanup_expired_records(
                report_cutoff=self._cutoff(
                    reference, self.settings.report_retention_days
                ),
                analysis_cutoff=self._cutoff(
                    reference, self.settings.analysis_retention_days
                ),
                alarm_cutoff=self._cutoff(
                    reference, self.settings.alarm_retention_days
                ),
                dispatch_cutoff=self._cutoff(
                    reference, self.settings.dispatch_retention_days
                ),
                audit_cutoff=self._cutoff(
                    reference, self.settings.audit_retention_days
                ),
                queued_at=current,
                batch_size=batch_size,
            )
            files = {"deleted": 0, "missing": 0, "failed": 0}
            for item in self.repository.list_file_deletions(batch_size):
                relative_path = str(item["relative_path"])
                try:
                    if self._delete_report_file(relative_path):
                        files["deleted"] += 1
                    else:
                        files["missing"] += 1
                    self.repository.complete_file_deletion(relative_path)
                except Exception as exc:
                    files["failed"] += 1
                    self.repository.fail_file_deletion(
                        relative_path,
                        str(exc),
                        current,
                    )
                    logger.exception(
                        "retained report file cleanup failed",
                        extra={"relative_path": relative_path},
                    )
            return {"skipped": False, "records": records, "files": files}
        finally:
            self._cleanup_lock.release()
