from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.dispatch import DispatchRepository
from app.database.pdm import PdmRepository
from app.services.file_download_service import DownloadError, FileDownloadService
from app.services.file_source import FileSource, normalize_relative_path
from app.services.magic_box_service import (
    MagicBoxClient,
    MagicBoxConfig,
    MagicBoxConfigProvider,
    MagicBoxError,
)


class CuttingDispatchError(RuntimeError):
    pass


class CuttingFileNotFoundError(CuttingDispatchError):
    pass


class DispatchNotFoundError(CuttingDispatchError):
    pass


class DispatchConflictError(CuttingDispatchError):
    pass


class DuplicateDispatchError(CuttingDispatchError):
    def __init__(self, dispatch: dict[str, Any]) -> None:
        self.dispatch = dispatch
        super().__init__(f"同一文件版本已存在进行中的派发任务：{dispatch['dispatch_id']}")


STATUS_LABELS = {
    "waiting_source": "等待文件服务",
    "ready": "待派发",
    "downloading": "正在准备文件",
    "downloaded": "文件已就绪",
    "pushing": "正在推送",
    "sent": "已派发",
    "download_failed": "文件准备失败",
    "push_failed": "推送失败",
    "cancelled": "已取消指派",
}

STAGE_LABELS = {
    "created": "已创建",
    "downloading": "准备文件",
    "file_ready": "文件校验完成",
    "uploading": "上传魔盒",
    "uploaded": "魔盒已接收",
    "assigning": "指派机床",
    "complete": "推送完成",
    "download_failed": "文件准备失败",
    "download_interrupted": "文件准备被中断",
    "upload_failed": "上传失败",
    "upload_unknown": "上传结果待核对",
    "assign_failed": "机床指派失败",
    "assign_interrupted": "机床指派被中断",
    "cancelled": "已取消指派",
}

PUSH_FILE_SUFFIXES = frozenset({".gen", ".txt"})


class CuttingDispatchService:
    def __init__(
        self,
        settings: Settings,
        pdm: PdmRepository,
        dispatches: DispatchRepository,
        file_source: FileSource,
        downloads: FileDownloadService | None = None,
        magic_config: MagicBoxConfigProvider | None = None,
        magic_client_factory: Callable[[MagicBoxConfig], MagicBoxClient] | None = None,
    ) -> None:
        self.settings = settings
        self.pdm = pdm
        self.dispatches = dispatches
        self.file_source = file_source
        self.downloads = downloads
        self.magic_config = magic_config
        self.magic_client_factory = magic_client_factory or (
            lambda config: MagicBoxClient(
                config,
                timeout=self.settings.magic_box_timeout,
                timezone=self.settings.timezone,
            )
        )

    def _now(self) -> str:
        return datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds")

    def status(self, *, include_magic: bool = True) -> dict[str, Any]:
        source_status = self.file_source.status()
        magic_status = (
            self.magic_config.public_status()
            if include_magic and self.magic_config is not None
            else {
                "configured": False,
                "gateway": None,
                "machine_ips": [],
                "message": (
                    "文件下载页无需检查魔盒配置。"
                    if not include_magic
                    else "魔盒客户端未初始化"
                ),
            }
        )
        can_download = bool(source_status["enabled"]) and not (
            self.file_source.mode == "http" and self.pdm.mode != "sqlserver"
        )
        real_sources = self.pdm.mode == "sqlserver" and self.file_source.mode != "mock"
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
            "magic_box": {**magic_status, "implemented": True},
            "can_query": True,
            "can_download": can_download,
            "can_push": include_magic and can_download and real_sources and bool(magic_status["configured"]),
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
        item.update({
            "status": status,
            "status_label": status_label,
            "normalized_path": normalized_path,
            "path_available": normalized_path is not None,
            "path_error": path_error,
        })
        return item

    def search_files(self, filters, *, page, page_size, sort=()):
        rows, total = self.pdm.search_files(filters, page=page, page_size=page_size, sort=sort)
        return {"items": [self._file_item(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def list_files(self, project_id: str, *, catalog_id: str | None, status: str, keyword: str, page: int, page_size: int) -> dict[str, Any]:
        rows, total = self.pdm.list_files(project_id, catalog_id=catalog_id, status=status, keyword=keyword, page=page, page_size=page_size)
        return {"items": [self._file_item(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def create_dispatch(
        self,
        *,
        document_id: str | None,
        download_id: str | None = None,
        machine_ip: str,
        material: str | None,
        thickness: float | None,
        quantity: int,
        user: dict,
    ) -> dict[str, Any]:
        selected_download = None
        if download_id:
            if self.downloads is None:
                raise CuttingDispatchError("文件下载服务未初始化")
            try:
                selected_download, local_path = self.downloads.local_file(download_id)
            except DownloadError as exc:
                raise CuttingDispatchError(str(exc)) from None
            document_id = str(selected_download["document_id"])
        if not document_id:
            raise CuttingDispatchError("请选择一个系统已保存文件")
        raw_file = self.pdm.get_file(document_id)
        if raw_file is None:
            raise CuttingFileNotFoundError("PDM中未找到指定切割文件")
        file_item = self._file_item(raw_file)
        if not file_item["path_available"]:
            raise CuttingDispatchError(file_item["path_error"])
        if selected_download is not None:
            if str(file_item.get("version_id") or "") != str(selected_download.get("version_id") or ""):
                raise CuttingDispatchError("系统已保存文件与PDM当前版本不一致，请回到文件下载页重新保存")
            if file_item["normalized_path"] != normalize_relative_path(selected_download["relative_path"]):
                raise CuttingDispatchError("系统已保存文件与PDM当前路径不一致，请重新保存")
        physical_suffix = (local_path if selected_download is not None else Path(file_item["normalized_path"])).suffix.lower()
        document_suffix = Path(str(file_item["document_no"])).suffix.lower()
        if physical_suffix not in PUSH_FILE_SUFFIXES and document_suffix not in PUSH_FILE_SUFFIXES:
            raise CuttingDispatchError("当前只允许创建.GEN或.TXT文件派发任务")
        existing = self.dispatches.find_active(document_id, file_item.get("version_id"), machine_ip)
        if existing:
            raise DuplicateDispatchError(existing)
        project = next((item for item in self.pdm.list_projects("", 100) if str(item["project_id"]) == str(file_item["project_id"])), None)
        dispatch = self.dispatches.create(
            {
                **file_item,
                "project_name": project.get("project_name") if project else None,
                "relative_path": file_item["normalized_path"],
                "machine_ip": machine_ip,
                "material": material,
                "thickness": thickness,
                "quantity": quantity,
                "status": "downloaded" if selected_download is not None else "ready" if self.file_source.enabled else "waiting_source",
                "stage": "file_ready" if selected_download is not None else "created",
                "source_mode": selected_download.get("source_mode", self.file_source.mode) if selected_download else self.file_source.mode,
                "is_simulation": bool(selected_download.get("is_simulation")) if selected_download else self.pdm.mode == "mock" or self.file_source.mode == "mock",
                "download_id": selected_download.get("download_id") if selected_download else None,
                "file_size": selected_download.get("size") if selected_download else None,
                "file_sha256": selected_download.get("sha256") if selected_download else None,
            },
            user=user,
            now=self._now(),
        )
        return self._decorate_dispatch(dispatch)

    @staticmethod
    def _decorate_dispatch(item: dict[str, Any]) -> dict[str, Any]:
        stage = item.get("stage") or "created"
        remote_id = bool(item.get("remote_task_id"))
        return {
            **item,
            "status_label": STATUS_LABELS.get(item["status"], item["status"]),
            "stage_label": STAGE_LABELS.get(stage, stage),
            "can_execute": item["status"] in {"ready", "downloaded", "download_failed"}
            or (item["status"] == "push_failed" and stage != "upload_unknown"),
            "can_cancel": remote_id and item["status"] in {"sent", "push_failed"},
        }

    def list_dispatches(self, *, page: int, page_size: int) -> dict[str, Any]:
        items, total = self.dispatches.list(page=page, page_size=page_size)
        return {"items": [self._decorate_dispatch(item) for item in items], "total": total, "page": page, "page_size": page_size}

    def list_machines(self) -> dict[str, Any]:
        if self.magic_config is None:
            raise MagicBoxError("魔盒客户端未初始化", status_code=503)
        config = self.magic_config.get()
        client = self.magic_client_factory(config)
        time_status = client.sync_time()
        online = {item["machine_ip"]: item for item in client.list_machines()}
        items = []
        for ip in config.machine_ips:
            remote = online.get(ip, {})
            items.append({
                "machine_ip": ip,
                "server_name": remote.get("server_name"),
                "alive_time": remote.get("alive_time"),
                "applications": remote.get("applications", []),
                "online": ip in online,
                "configured": True,
            })
        return {"items": items, "time": time_status}

    def _transition(self, dispatch_id: str, **kwargs) -> dict[str, Any]:
        item = self.dispatches.transition(dispatch_id, **kwargs)
        if item is None:
            raise DispatchConflictError("派发状态已变化，请刷新记录后重试")
        return item

    def _prepare_local_file(self, item: dict[str, Any], user: dict) -> tuple[dict[str, Any], Path]:
        if self.downloads is None:
            raise CuttingDispatchError("文件下载服务未初始化")
        if item.get("download_id"):
            try:
                _record, path = self.downloads.local_file(item["download_id"])
                return item, path
            except DownloadError:
                pass
        item = self._transition(item["dispatch_id"], expected={item["status"]}, status="downloading", stage="downloading", now=self._now())
        try:
            downloaded, path = self.downloads.get_or_download(
                document_id=item["document_id"],
                version_id=item.get("version_id"),
                file_path=item["relative_path"],
                user=user,
            )
        except DownloadError as exc:
            self._transition(item["dispatch_id"], expected={"downloading"}, status="download_failed", stage="download_failed", last_error=str(exc), now=self._now())
            raise CuttingDispatchError(str(exc)) from None
        item = self._transition(
            item["dispatch_id"], expected={"downloading"}, status="downloaded", stage="file_ready",
            download_id=downloaded["download_id"], file_size=int(downloaded["size"]),
            file_sha256=downloaded["sha256"], now=self._now(),
        )
        return item, path

    def execute_dispatch(self, dispatch_id: str, *, user: dict) -> dict[str, Any]:
        item = self.dispatches.get(dispatch_id)
        if item is None:
            raise DispatchNotFoundError("派发任务不存在")
        if item["status"] == "waiting_source":
            raise DispatchConflictError("文件下载服务尚未可用")
        if item.get("is_simulation"):
            raise DispatchConflictError("模拟PDM或模拟文件不允许推送到真实魔盒")
        if item["status"] in {"sent", "cancelled", "downloading", "pushing"}:
            raise DispatchConflictError(f"当前状态“{STATUS_LABELS[item['status']]}”不允许再次推送")
        if item["status"] == "push_failed" and item.get("stage") == "upload_unknown":
            raise DispatchConflictError("上次上传结果未知，请先在魔盒中按任务标识核对，禁止盲目重复上传")
        if self.magic_config is None:
            raise MagicBoxError("魔盒客户端未初始化", status_code=503)
        config = self.magic_config.get()
        if item["machine_ip"] not in config.machine_ips:
            raise DispatchConflictError("目标机床不在SYS_CONFIG的CUTTER01_IP/CUTTER02_IP白名单中")

        local_path: Path | None = None
        if not item.get("remote_task_id"):
            item, local_path = self._prepare_local_file(item, user)

        client = self.magic_client_factory(config)
        client.sync_time()
        if not item.get("remote_task_id"):
            item = self._transition(dispatch_id, expected={"downloaded", "push_failed"}, status="pushing", stage="uploading", increment_attempt=True, now=self._now())
            try:
                assert local_path is not None
                remote_id = client.upload_task(
                    task_identifier=item["task_identifier"],
                    task_name=local_path.name,
                    file_path=local_path,
                    material=item.get("material"),
                    thickness=item.get("thickness"),
                    count=int(item["quantity"]),
                )
            except MagicBoxError as exc:
                self._transition(dispatch_id, expected={"pushing"}, status="push_failed", stage="upload_unknown" if exc.outcome_unknown else "upload_failed", last_error=str(exc), now=self._now())
                raise
            item = self._transition(dispatch_id, expected={"pushing"}, status="pushing", stage="uploaded", remote_task_id=remote_id, now=self._now())

        item = self._transition(dispatch_id, expected={"pushing", "push_failed"}, status="pushing", stage="assigning", now=self._now())
        try:
            client.assign_task(task_identifier=item["task_identifier"], machine_ip=item["machine_ip"])
        except MagicBoxError as exc:
            self._transition(dispatch_id, expected={"pushing"}, status="push_failed", stage="assign_failed", last_error=str(exc), now=self._now())
            raise
        item = self._transition(dispatch_id, expected={"pushing"}, status="sent", stage="complete", now=self._now())
        return self._decorate_dispatch(item)

    def cancel_dispatch(self, dispatch_id: str) -> dict[str, Any]:
        item = self.dispatches.get(dispatch_id)
        if item is None:
            raise DispatchNotFoundError("派发任务不存在")
        if not item.get("remote_task_id") or item["status"] not in {"sent", "push_failed"}:
            raise DispatchConflictError("当前任务没有可取消的魔盒指派")
        if self.magic_config is None:
            raise MagicBoxError("魔盒客户端未初始化", status_code=503)
        config = self.magic_config.get()
        client = self.magic_client_factory(config)
        client.sync_time()
        client.cancel_assignment(task_identifier=item["task_identifier"])
        item = self._transition(dispatch_id, expected={item["status"]}, status="cancelled", stage="cancelled", now=self._now())
        return self._decorate_dispatch(item)
