from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.api.cutting import DispatchBatchCreateRequest
from app.config import settings
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import WorkshopRepository
from app.services.cutting_dispatch_service import (
    CuttingDispatchService,
    CuttingDispatchError,
    DuplicateDispatchError,
)
from app.services.file_download_service import FileDownloadService
from app.services.file_source import (
    DisabledFileSource,
    LocalFileSource,
    MockFileSource,
    normalize_relative_path,
)
from app.services.magic_box_service import MagicBoxConfig, MagicBoxError


def _service(tmp_path: Path, file_source=None):
    configured = replace(settings, project_root=tmp_path)
    database = SQLiteDatabase(tmp_path / "metadata.db")
    workshop = WorkshopRepository(database)
    workshop.initialize()
    dispatches = DispatchRepository(database)
    dispatches.initialize()
    admin = workshop.create_user(
        username="admin",
        display_name="管理员",
        password="correct-horse-battery-staple",
        role="admin",
        now="2026-08-28T09:00:00+08:00",
    )
    return (
        CuttingDispatchService(
            configured,
            MockPdmRepository(),
            dispatches,
            file_source or DisabledFileSource(),
        ),
        dispatches,
        admin,
    )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "",
        "../secret.GEN",
        "CMYZ0177/../../secret.GEN",
        "/etc/passwd",
        r"D:\File\secret.GEN",
        "folder/\x00secret.GEN",
    ],
)
def test_relative_path_normalization_rejects_unsafe_paths(unsafe_path: str) -> None:
    with pytest.raises(ValueError):
        normalize_relative_path(unsafe_path)


def test_relative_path_normalization_accepts_database_path() -> None:
    assert (
        normalize_relative_path(
            r"CMYZ0177\PK-707C1\f7e50f62-d508-430e-8309-8bf309568095.GEN"
        )
        == "CMYZ0177/PK-707C1/f7e50f62-d508-430e-8309-8bf309568095.GEN"
    )


def test_local_file_source_stays_inside_read_only_root(tmp_path: Path) -> None:
    root = tmp_path / "files"
    source = root / "CMYZ0177" / "sample.GEN"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"GEN DATA")
    target = tmp_path / "download" / "sample.GEN"
    file_source = LocalFileSource(root)

    assert file_source.exists(r"CMYZ0177\sample.GEN") is True
    assert file_source.download(r"CMYZ0177\sample.GEN", target) == target
    assert target.read_bytes() == b"GEN DATA"
    with pytest.raises(ValueError):
        file_source.resolve("../outside.GEN")


def test_mock_pdm_supports_project_catalog_file_and_status_queries(tmp_path: Path) -> None:
    service, _dispatches, _admin = _service(tmp_path)
    project = service.list_projects("0177", 10)[0]
    catalogs = service.list_catalogs(project["project_id"])
    files = service.list_files(
        project["project_id"],
        catalog_id="CAT-GEN",
        status="pending",
        keyword="f7e50",
        page=1,
        page_size=10,
    )

    assert catalogs["items"][-1]["level"] == 2
    assert files["total"] == 1
    assert files["items"][0]["path_available"] is True
    assert files["items"][0]["status_label"] == "待派发"


def test_download_status_does_not_query_magic_box_configuration(tmp_path: Path) -> None:
    service, _dispatches, _admin = _service(tmp_path)

    class UnexpectedMagicConfig:
        def public_status(self):
            raise AssertionError("download status must not query production SYS_CONFIG")

    service.magic_config = UnexpectedMagicConfig()
    result = service.status(include_magic=False)

    assert result["magic_box"]["configured"] is False
    assert result["can_push"] is False
    assert result["can_query"] is True


def test_saving_dispatch_is_persistent_but_does_not_download(tmp_path: Path) -> None:
    service, dispatches, admin = _service(tmp_path)

    item = service.create_dispatch(
        document_id="F1A0796D-7118-4E37-8F69-B6D917BD6456",
        machine_ip="10.1.133.196",
        material="AH36",
        thickness=12.0,
        quantity=2,
        user=admin,
    )

    assert item["status"] == "waiting_source"
    assert item["status_label"] == "等待文件服务"
    assert item["source_mode"] == "disabled"
    assert dispatches.get(item["dispatch_id"])["document_no"].endswith(".GEN")

    with pytest.raises(DuplicateDispatchError):
        service.create_dispatch(
            document_id="F1A0796D-7118-4E37-8F69-B6D917BD6456",
            machine_ip="10.1.133.196",
            material=None,
            thickness=None,
            quantity=1,
            user=admin,
        )


@pytest.mark.parametrize("suffix", [".GEN", ".gen", ".TXT", ".txt"])
def test_dispatch_accepts_supported_file_types(tmp_path: Path, suffix: str) -> None:
    service, _dispatches, admin = _service(tmp_path)
    file = service.pdm.files[0]
    file["document_no"] = f"cutting-task{suffix}"
    file["file_path"] = f"CMYZ0177\\PK-707C1\\cutting-task{suffix}"

    item = service.create_dispatch(
        document_id=file["document_id"],
        machine_ip="10.1.133.196",
        material=None,
        thickness=None,
        quantity=1,
        user=admin,
    )

    assert item["document_no"] == f"cutting-task{suffix}"


def test_dispatch_rejects_unsupported_file_type(tmp_path: Path) -> None:
    service, _dispatches, admin = _service(tmp_path)
    file = service.pdm.files[0]
    file["document_no"] = "drawing.PDF"
    file["file_path"] = "CMYZ0177\\PK-707C1\\drawing.PDF"

    with pytest.raises(CuttingDispatchError, match=r"\.GEN或\.TXT"):
        service.create_dispatch(
            document_id=file["document_id"],
            machine_ip="10.1.133.196",
            material=None,
            thickness=None,
            quantity=1,
            user=admin,
        )


def test_enabled_mock_source_creates_ready_task_and_state_transition(tmp_path: Path) -> None:
    service, dispatches, admin = _service(tmp_path, MockFileSource())
    item = service.create_dispatch(
        document_id="F1A0796D-7118-4E37-8F69-B6D917BD6456",
        machine_ip="10.1.133.197",
        material=None,
        thickness=None,
        quantity=1,
        user=admin,
    )

    assert item["status"] == "ready"
    downloading = dispatches.transition(
        item["dispatch_id"],
        expected={"ready"},
        status="downloading",
        now="2026-08-28T09:01:00+08:00",
    )
    assert downloading["status"] == "downloading"
    assert (
        dispatches.transition(
            item["dispatch_id"],
            expected={"ready"},
            status="downloaded",
            now="2026-08-28T09:02:00+08:00",
        )
        is None
    )


class FixedMagicConfig:
    config = MagicBoxConfig(
        gateway="http://magic.internal:8080",
        token="token",
        secret="secret",
        machine_ips=("10.1.133.197",),
    )

    def get(self):
        return self.config

    def public_status(self):
        return {
            "configured": True,
            "gateway": self.config.gateway,
            "machine_ips": list(self.config.machine_ips),
            "message": "ok",
        }


class RecordingMagicClient:
    def __init__(self, *, fail_assign=False):
        self.fail_assign = fail_assign
        self.uploads = []
        self.assignments = []

    def sync_time(self):
        return {"box_time": "2026-09-01T08:00:00", "offset_seconds": 0}

    def list_machines(self):
        return []

    def upload_task(self, **kwargs):
        self.uploads.append(kwargs)
        return "magic-uuid-1"

    def assign_task(self, **kwargs):
        self.assignments.append(kwargs)
        if self.fail_assign:
            raise MagicBoxError("指派失败")

    def cancel_assignment(self, **kwargs):
        return None


class TestHttpFileSource(MockFileSource):
    mode = "http"


def _push_service(tmp_path: Path, client: RecordingMagicClient):
    cfg = replace(settings, project_root=tmp_path, magic_box_enabled=True)
    database = SQLiteDatabase(tmp_path / "metadata.db")
    workshop = WorkshopRepository(database)
    workshop.initialize()
    dispatches = DispatchRepository(database)
    dispatches.initialize()
    download_repository = DownloadRepository(database)
    download_repository.initialize()
    pdm = MockPdmRepository()
    pdm.mode = "sqlserver"
    source = TestHttpFileSource()
    downloads = FileDownloadService(cfg, download_repository, pdm, source)
    admin = workshop.create_user(
        username="push-admin",
        display_name="推送管理员",
        password="correct-horse-battery-staple",
        role="admin",
        now="2026-09-01T08:00:00+08:00",
    )
    service = CuttingDispatchService(
        cfg, pdm, dispatches, source, downloads, FixedMagicConfig(), lambda _: client
    )
    return service, dispatches, admin


def test_real_push_uses_uploaded_file_name_as_task_name(tmp_path: Path):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    draft = service.create_dispatch(
        document_id="F1A0796D-7118-4E37-8F69-B6D917BD6456",
        machine_ip="10.1.133.197",
        material="AH36",
        thickness=12,
        quantity=2,
        user=admin,
    )

    sent = service.execute_dispatch(draft["dispatch_id"], user=admin)

    assert sent["status"] == "sent"
    assert sent["remote_task_id"] == "magic-uuid-1"
    assert client.uploads[0]["task_name"] == client.uploads[0]["file_path"].name
    assert client.uploads[0]["task_name"].endswith(".GEN")
    assert client.assignments == [{
        "task_identifier": draft["task_identifier"],
        "machine_ip": "10.1.133.197",
    }]
    stored = dispatches.get(draft["dispatch_id"])
    assert stored["stage"] == "complete" and stored["attempt_count"] == 1


def test_push_can_start_from_selected_download_without_file_search(tmp_path: Path):
    client = RecordingMagicClient()
    service, _dispatches, admin = _push_service(tmp_path, client)
    raw = service.pdm.get_file("F1A0796D-7118-4E37-8F69-B6D917BD6456")
    downloaded = service.downloads.download(
        document_id=raw["document_id"],
        version_id=raw["version_id"],
        file_path=raw["file_path"],
        user=admin,
    )

    draft = service.create_dispatch(
        document_id=None,
        download_id=downloaded["download_id"],
        machine_ip="10.1.133.197",
        material="AH36",
        thickness=10,
        quantity=1,
        user=admin,
    )
    sent = service.execute_dispatch(draft["dispatch_id"], user=admin)

    assert draft["status"] == "downloaded"
    assert sent["status"] == "sent"
    assert client.uploads[0]["task_name"] == downloaded["filename"]


def test_batch_creation_isolates_files_and_reports_duplicates(tmp_path: Path):
    client = RecordingMagicClient()
    service, _dispatches, admin = _push_service(tmp_path, client)
    downloaded = []
    for raw in service.pdm.files[:2]:
        downloaded.append(
            service.downloads.download(
                document_id=raw["document_id"],
                version_id=raw["version_id"],
                file_path=raw["file_path"],
                user=admin,
            )
        )

    payload = [
        {
            "download_id": item["download_id"],
            "material": "AH36",
            "thickness": 10,
            "quantity": index,
        }
        for index, item in enumerate(downloaded, start=1)
    ]
    created = service.create_dispatch_batch(
        machine_ip="10.1.133.197", items=payload, user=admin
    )

    assert created["created_count"] == 2
    assert created["duplicate_count"] == created["failed_count"] == 0
    assert [item["status"] for item in created["items"]] == ["created", "created"]
    assert [item["dispatch"]["quantity"] for item in created["items"]] == [1, 2]

    repeated = service.create_dispatch_batch(
        machine_ip="10.1.133.197", items=payload, user=admin
    )

    assert repeated["created_count"] == 0
    assert repeated["duplicate_count"] == 2
    assert repeated["failed_count"] == 0
    assert all(item["dispatch"]["dispatch_id"] for item in repeated["items"])


def test_batch_request_limits_files_and_requires_complete_parameters():
    valid_item = {
        "download_id": f"dl_{'a' * 32}",
        "material": "AH36",
        "thickness": 10,
        "quantity": 1,
    }
    payload = DispatchBatchCreateRequest(
        machine_ip="10.1.133.197",
        items=[
            {**valid_item, "download_id": f"dl_{index:032x}"}
            for index in range(20)
        ],
    )
    assert len(payload.items) == 20

    with pytest.raises(ValidationError):
        DispatchBatchCreateRequest(
            machine_ip="10.1.133.197",
            items=[
                {**valid_item, "download_id": f"dl_{index:032x}"}
                for index in range(21)
            ],
        )
    with pytest.raises(ValidationError):
        DispatchBatchCreateRequest(
            machine_ip="10.1.133.197",
            items=[{**valid_item, "material": "", "thickness": 0}],
        )


def test_assign_retry_does_not_upload_file_twice(tmp_path: Path):
    client = RecordingMagicClient(fail_assign=True)
    service, dispatches, admin = _push_service(tmp_path, client)
    draft = service.create_dispatch(
        document_id="F1A0796D-7118-4E37-8F69-B6D917BD6456",
        machine_ip="10.1.133.197",
        material=None,
        thickness=None,
        quantity=1,
        user=admin,
    )
    with pytest.raises(MagicBoxError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    assert dispatches.get(draft["dispatch_id"])["stage"] == "assign_failed"

    client.fail_assign = False
    sent = service.execute_dispatch(draft["dispatch_id"], user=admin)

    assert sent["status"] == "sent"
    assert len(client.uploads) == 1
    assert len(client.assignments) == 2
