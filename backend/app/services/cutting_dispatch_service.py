from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.dispatch import DispatchRepository
from app.database.pdm import PdmRepository
from app.services.file_source import FileSource, normalize_relative_path


class CuttingDispatchError(RuntimeError):
    pass


class CuttingFileNotFoundError(CuttingDispatchError):
    pass


class DuplicateDispatchError(CuttingDispatchError):
    def __init__(self, dispatch: dict[str, Any]) -> None:
        self.dispatch = dispatch
        super().__init__(f"同一文件版本已存在进行中的派发任务：{dispatch['dispatch_id']}")


STATUS_LABELS = {
    "waiting_source": "等待文件服务",
    "ready": "待派发",
    "downloading": "正在下载",
    "downloaded": "下载完成",
    "pushing": "正在推送",
    "sent": "已派发",
    "download_failed": "下载失败",
    "push_failed": "推送失败",
    "cancelled": "已取消",
}


class CuttingDispatchService:
    def __init__(
        self,
        settings: Settings,
        pdm: PdmRepository,
        dispatches: DispatchRepository,
        file_source: FileSource,
    ) -> None:
        self.settings = settings
        self.pdm = pdm
        self.dispatches = dispatches
        self.file_source = file_source

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.settings.timezone)).isoformat(
            timespec="seconds"
        )

    def status(self) -> dict[str, Any]:
        source_status = self.file_source.status()
        return {
            "pdm": {
                "mode": self.pdm.mode,
                "enabled": self.pdm.mode == "sqlserver",
                "label": "PDM SQL Server" if self.pdm.mode == "sqlserver" else "模拟PDM数据",
            },
            "file_source": source_status,
            "file_server": {
                "host": self.settings.file_server_host or None,
                "physical_root": self.settings.file_server_physical_root or None,
            },
            "magic_box": {
                "configured": self.settings.magic_box_enabled,
                "implemented": False,
                "label": (
                    "配置开关已开启，客户端尚未接入"
                    if self.settings.magic_box_enabled
                    else "尚未配置"
                ),
            },
            "can_query": True,
            "can_download": bool(source_status["enabled"]) and not (self.file_source.mode == "http" and self.pdm.mode != "sqlserver"),
            "can_push": False,
            "safe_mode": True,
        }

    @staticmethod
    def _serializable(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.isoformat(timespec="seconds") if hasattr(value, "isoformat") else value
            for key, value in item.items()
        }

    def list_projects(self, keyword: str, limit: int) -> list[dict[str, Any]]:
        return [self._serializable(item) for item in self.pdm.list_projects(keyword, limit)]

    def list_catalogs(self, project_id: str) -> dict[str, Any]:
        items = [self._serializable(item) for item in self.pdm.list_catalogs(project_id)]
        by_id = {str(item["catalog_id"]): {**item, "children": []} for item in items}
        roots: list[dict[str, Any]] = []
        for item in by_id.values():
            parent_id = item.get("parent_id")
            parent = by_id.get(str(parent_id)) if parent_id else None
            if parent:
                parent["children"].append(item)
            else:
                roots.append(item)

        flat: list[dict[str, Any]] = []

        def visit(nodes: list[dict[str, Any]], level: int) -> None:
            for node in nodes:
                children = node["children"]
                flat.append({key: value for key, value in node.items() if key != "children"} | {"level": level})
                visit(children, level + 1)

        visit(roots, 0)
        return {"items": flat, "tree": roots}

    def _file_status(self, value: Any) -> tuple[str, str]:
        cleaned = str(value or "").strip()
        if not cleaned:
            return "pending", "待派发"
        if cleaned == self.settings.file_status_failed_value:
            return "failed", self.settings.file_status_failed_value
        if cleaned == self.settings.file_status_sent_value:
            return "sent", self.settings.file_status_sent_value
        return "other", cleaned

    def _file_item(self, raw: dict[str, Any]) -> dict[str, Any]:
        item = self._serializable(raw)
        status, status_label = self._file_status(item.get("custom6"))
        file_path = str(item.get("file_path") or "").strip()
        path_error = None
        normalized_path = None
        if file_path:
            try:
                normalized_path = normalize_relative_path(file_path)
            except ValueError as exc:
                path_error = str(exc)
        else:
            path_error = "未查询到文件相对路径"
        if int(item.get("physical_file_count") or 0) > 1:
            normalized_path = None
            path_error = "当前文档版本对应多个文件，请先确认唯一的文件关联"
        item.update(
            {
                "status": status,
                "status_label": status_label,
                "normalized_path": normalized_path,
                "path_available": normalized_path is not None,
                "path_error": path_error,
            }
        )
        return item

    def search_files(self, filters, *, page, page_size):
        rows, total = self.pdm.search_files(filters, page=page, page_size=page_size)
        return {"items": [self._file_item(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def list_files(
        self,
        project_id: str,
        *,
        catalog_id: str | None,
        status: str,
        keyword: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        rows, total = self.pdm.list_files(
            project_id,
            catalog_id=catalog_id,
            status=status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
        return {
            "items": [self._file_item(row) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    def create_dispatch(
        self,
        *,
        document_id: str,
        machine_ip: str,
        material: str | None,
        thickness: float | None,
        quantity: int,
        user: dict,
    ) -> dict[str, Any]:
        raw_file = self.pdm.get_file(document_id)
        if raw_file is None:
            raise CuttingFileNotFoundError("PDM中未找到指定切割文件")
        file_item = self._file_item(raw_file)
        if not file_item["path_available"]:
            raise CuttingDispatchError(file_item["path_error"])
        if not str(file_item["document_no"]).lower().endswith(".gen"):
            raise CuttingDispatchError("当前阶段只允许创建.GEN文件派发任务")
        existing = self.dispatches.find_active(
            document_id,
            file_item.get("version_id"),
            machine_ip,
        )
        if existing:
            raise DuplicateDispatchError(existing)
        project = next(
            (
                item
                for item in self.pdm.list_projects("", 100)
                if str(item["project_id"]) == str(file_item["project_id"])
            ),
            None,
        )
        dispatch = self.dispatches.create(
            {
                **file_item,
                "project_name": project.get("project_name") if project else None,
                "relative_path": file_item["normalized_path"],
                "machine_ip": machine_ip,
                "material": material,
                "thickness": thickness,
                "quantity": quantity,
                "status": "ready" if self.file_source.enabled else "waiting_source",
                "source_mode": self.file_source.mode,
                "is_simulation": self.pdm.mode == "mock" or self.file_source.mode == "mock",
            },
            user=user,
            now=self._now(),
        )
        return self._decorate_dispatch(dispatch)

    @staticmethod
    def _decorate_dispatch(item: dict[str, Any]) -> dict[str, Any]:
        return {**item, "status_label": STATUS_LABELS.get(item["status"], item["status"])}

    def list_dispatches(self, *, page: int, page_size: int) -> dict[str, Any]:
        items, total = self.dispatches.list(page=page, page_size=page_size)
        return {
            "items": [self._decorate_dispatch(item) for item in items],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
