from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.cutting import (
    DispatchBatchCreateRequest,
    DispatchCreateRequest,
    create_dispatch as create_dispatch_api,
    get_dispatch as get_dispatch_api,
)
from app.config import settings
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import WorkshopRepository
from app.services.cutting_dispatch_service import (
    CuttingDispatchService,
    CuttingDispatchError,
    DispatchConflictError,
    DispatchNotFoundError,
    DuplicateDispatchError,
    standard_instruction_parameters,
)
from app.services.file_download_service import DownloadError, FileDownloadService
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


def test_file_list_keeps_selected_path_available_with_physical_history(tmp_path: Path) -> None:
    service, _dispatches, _admin = _service(tmp_path)
    service.pdm.files[0]["physical_file_count"] = 3

    files = service.list_files(
        service.pdm.projects[0]["project_id"],
        catalog_id="CAT-GEN",
        status="pending",
        keyword="f7e50",
        page=1,
        page_size=10,
    )

    assert files["items"][0]["path_available"] is True
    assert files["items"][0]["normalized_path"].endswith("f7e50f62-d508-430e-8309-8bf309568095.GEN")


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


def test_concurrent_duplicate_dispatch_creation_is_atomic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service, dispatches, admin = _service(tmp_path)
    original_create = dispatches.create_unless_active
    ready = Barrier(2)

    def synchronized_create(*args, **kwargs):
        ready.wait(timeout=5)
        return original_create(*args, **kwargs)

    monkeypatch.setattr(dispatches, "create_unless_active", synchronized_create)

    def create() -> tuple[str, str]:
        try:
            item = service.create_dispatch(
                document_id="F1A0796D-7118-4E37-8F69-B6D917BD6456",
                machine_ip="10.1.133.196",
                material="AH36",
                thickness=12.0,
                quantity=1,
                user=admin,
            )
            return "created", item["dispatch_id"]
        except DuplicateDispatchError as exc:
            return "duplicate", exc.dispatch["dispatch_id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create(), range(2)))

    assert sorted(status for status, _dispatch_id in results) == [
        "created",
        "duplicate",
    ]
    assert len({dispatch_id for _status, dispatch_id in results}) == 1
    items, total = dispatches.list(page=1, page_size=10)
    assert total == 1
    assert items[0]["dispatch_id"] == results[0][1]


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
    assert sent["local_file_cleanup"] == {
        "deleted": True,
        "already_deleted": False,
        "error": None,
    }
    stored_download = service.downloads.repository.get(downloaded["download_id"])
    assert stored_download["cleanup_reason"] == "pushed"
    assert stored_download["cleanup_dispatch_id"] == draft["dispatch_id"]
    assert service.downloads.public(stored_download)["available"] is False
    with pytest.raises(DownloadError, match="推送成功后删除"):
        service.downloads.local_file(downloaded["download_id"])


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
            "name_check_confirmed": True,
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


def test_batch_can_create_and_execute_directly_from_pdm_files(tmp_path: Path):
    client = RecordingMagicClient()
    service, _dispatches, admin = _push_service(tmp_path, client)
    payload = [
        {
            "document_id": raw["document_id"],
            "version_id": raw["version_id"],
            "file_path": raw["file_path"],
            "material": "AH36",
            "thickness": 10,
            "quantity": index,
            "name_check_confirmed": True,
        }
        for index, raw in enumerate(service.pdm.files[:2], start=1)
    ]

    created = service.create_dispatch_batch(
        machine_ip="10.1.133.197", items=payload, user=admin
    )
    sent = [
        service.execute_dispatch(item["dispatch"]["dispatch_id"], user=admin)
        for item in created["items"]
    ]

    assert created["created_count"] == 2
    assert [item["document_id"] for item in created["items"]] == [
        item["document_id"] for item in payload
    ]
    assert all(item["download_id"] is None for item in created["items"])
    assert [item["status"] for item in sent] == ["sent", "sent"]
    assert len(client.uploads) == len(client.assignments) == 2


def test_direct_batch_rechecks_selected_pdm_version_and_path(tmp_path: Path):
    client = RecordingMagicClient()
    service, _dispatches, admin = _push_service(tmp_path, client)
    raw = service.pdm.files[0]
    common = {
        "document_id": raw["document_id"],
        "version_id": raw["version_id"],
        "file_path": raw["file_path"],
        "material": "AH36",
        "thickness": 10,
        "quantity": 1,
    }

    for changed in (
        {**common, "version_id": "stale-version"},
        {**common, "file_path": "another/file.GEN"},
    ):
        result = service.create_dispatch_batch(
            machine_ip="10.1.133.197", items=[changed], user=admin
        )
        assert result["failed_count"] == 1
        assert "已变化" in result["items"][0]["error"]


def test_standard_name_parser_matches_browser_rules() -> None:
    assert standard_instruction_parameters(r"folder\P123C120A601.GEN") == ("AH36", 12.0)
    assert standard_instruction_parameters("s001c080ss99.txt") == ("双相不锈钢", 8.0)
    assert standard_instruction_parameters("custom.GEN") is None
    assert standard_instruction_parameters("P123C000A601.GEN") is None


def test_batch_requires_manual_confirmation_for_nonstandard_and_overridden_names(tmp_path: Path) -> None:
    service, dispatches, admin = _service(tmp_path)
    raw = service.pdm.files[0]
    common = {
        "document_id": raw["document_id"],
        "version_id": raw["version_id"],
        "file_path": raw["file_path"],
        "material": "AH36",
        "thickness": 12.0,
        "quantity": 1,
    }
    rejected = service.create_dispatch_batch(machine_ip="10.1.133.197", items=[common], user=admin)
    assert rejected["failed_count"] == 1
    assert "非标准文件名" in rejected["items"][0]["error"]
    assert dispatches.list(page=1, page_size=10)[1] == 0

    raw["document_no"] = "P123C120A601.GEN"
    standard = service.create_dispatch_batch(machine_ip="10.1.133.197", items=[common], user=admin)
    assert standard["created_count"] == 1
    assert service.get_dispatch(standard["items"][0]["dispatch"]["dispatch_id"])["can_execute"] is False
    with pytest.raises(DispatchNotFoundError):
        service.get_dispatch("missing-dispatch")


def test_standard_name_parameter_mismatch_requires_explicit_confirmation(tmp_path: Path) -> None:
    service, dispatches, admin = _service(tmp_path)
    raw = service.pdm.files[0]
    raw["document_no"] = "P123C120A601.GEN"
    common = {
        "document_id": raw["document_id"],
        "version_id": raw["version_id"],
        "file_path": raw["file_path"],
        "material": "AH36",
        "thickness": 10.0,
        "quantity": 1,
    }
    rejected = service.create_dispatch_batch(machine_ip="10.1.133.197", items=[common], user=admin)
    assert rejected["failed_count"] == 1
    assert "参数与标准文件名不一致" in rejected["items"][0]["error"]
    assert dispatches.list(page=1, page_size=10)[1] == 0

    confirmed = service.create_dispatch_batch(
        machine_ip="10.1.133.197",
        items=[{**common, "name_check_confirmed": True}],
        user=admin,
    )
    assert confirmed["created_count"] == 1
    assert confirmed["items"][0]["dispatch"]["thickness"] == 10.0


def test_single_dispatch_api_enforces_name_confirmation_and_exposes_current_status(tmp_path: Path) -> None:
    service, _dispatches, admin = _service(tmp_path)
    raw = service.pdm.files[0]
    request = SimpleNamespace(
        state=SimpleNamespace(request_id="dispatch-test"),
        app=SimpleNamespace(state=SimpleNamespace(cutting_dispatch=service)),
    )
    payload = DispatchCreateRequest(
        document_id=raw["document_id"],
        machine_ip="10.1.133.197",
        material="AH36",
        thickness=12,
        quantity=1,
    )
    with pytest.raises(HTTPException) as rejected:
        create_dispatch_api(payload, request, admin)
    assert rejected.value.status_code == 422
    assert "非标准文件名" in rejected.value.detail

    payload.name_check_confirmed = True
    created = create_dispatch_api(payload, request, admin)["data"]
    current = get_dispatch_api(created["dispatch_id"], request)["data"]
    assert current["dispatch_id"] == created["dispatch_id"]
    assert current["status"] == "waiting_source"


def test_batch_request_limits_files_and_validates_file_references():
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

    direct_item = {
        "document_id": "pdm-document-1",
        "version_id": "version-1",
        "file_path": "project/file.GEN",
        "material": "AH36",
        "thickness": 10,
        "quantity": 1,
    }
    assert DispatchBatchCreateRequest(
        machine_ip="10.1.133.197", items=[direct_item]
    ).items[0].document_id == "pdm-document-1"
    for invalid in (
        {**direct_item, "file_path": None},
        {**direct_item, "download_id": f"dl_{'b' * 32}"},
        {key: value for key, value in direct_item.items() if key != "document_id"},
    ):
        with pytest.raises(ValidationError):
            DispatchBatchCreateRequest(
                machine_ip="10.1.133.197", items=[invalid]
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
    assert service.get_dispatch(draft["dispatch_id"])["can_execute"] is True

    client.fail_assign = False
    sent = service.execute_dispatch(draft["dispatch_id"], user=admin)

    assert sent["status"] == "sent"
    assert len(client.uploads) == 1
    assert len(client.assignments) == 2


def test_unknown_upload_result_cannot_be_blindly_retried(tmp_path: Path) -> None:
    class UnknownUploadClient(RecordingMagicClient):
        def upload_task(self, **kwargs):
            self.uploads.append(kwargs)
            raise MagicBoxError("上传响应丢失", outcome_unknown=True)

    client = UnknownUploadClient()
    service, _dispatches, admin = _push_service(tmp_path, client)
    raw = service.pdm.files[0]
    created = service.create_dispatch_batch(
        machine_ip="10.1.133.197",
        items=[{
            "document_id": raw["document_id"],
            "version_id": raw["version_id"],
            "file_path": raw["file_path"],
            "material": "AH36",
            "thickness": 12,
            "quantity": 1,
            "name_check_confirmed": True,
        }],
        user=admin,
    )
    dispatch_id = created["items"][0]["dispatch"]["dispatch_id"]

    with pytest.raises(MagicBoxError, match="上传响应丢失"):
        service.execute_dispatch(dispatch_id, user=admin)
    current = service.get_dispatch(dispatch_id)
    assert current["stage"] == "upload_unknown"
    assert current["can_execute"] is False
    with pytest.raises(DispatchConflictError, match="禁止盲目重复上传"):
        service.execute_dispatch(dispatch_id, user=admin)
    assert len(client.uploads) == 1


def test_unknown_upload_blocks_replacement_and_survives_restart(tmp_path: Path):
    class UnknownClient(RecordingMagicClient):
        def upload_task(self, **kwargs):
            self.uploads.append(kwargs)
            raise MagicBoxError("上传响应丢失", outcome_unknown=True)

    client = UnknownClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    arguments = dict(document_id=service.pdm.files[0]["document_id"],
                     machine_ip="10.1.133.197", material=None, thickness=None,
                     quantity=1, user=admin)
    first = service.create_dispatch(**arguments)
    with pytest.raises(MagicBoxError):
        service.execute_dispatch(first["dispatch_id"], user=admin)
    dispatches.initialize()
    dispatches.recover_interrupted(service._now())
    with pytest.raises(DuplicateDispatchError) as duplicate:
        service.create_dispatch(**arguments)
    assert duplicate.value.dispatch["dispatch_id"] == first["dispatch_id"]

    def create_again():
        try:
            service.create_dispatch(**arguments)
        except DuplicateDispatchError as error:
            return error.dispatch["dispatch_id"]
        pytest.fail("uncertain upload must block every replacement")

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: create_again(), range(2))) == [first["dispatch_id"]] * 2
    raw = service.pdm.files[0]
    batch = service.create_dispatch_batch(machine_ip="10.1.133.197", user=admin,
        items=[dict(document_id=raw["document_id"], version_id=raw["version_id"],
                    file_path=raw["file_path"], material="AH36", thickness=12,
                    quantity=1, name_check_confirmed=True)])
    assert batch["items"][0]["status"] == "duplicate"
    assert dispatches.list(page=1, page_size=10)[1] == 1
    assert len(client.uploads) == 1


def test_concurrent_assign_retries_have_one_remote_executor(tmp_path: Path):
    client = RecordingMagicClient(fail_assign=True)
    service, dispatches, admin = _push_service(tmp_path, client)
    draft = service.create_dispatch(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    with pytest.raises(MagicBoxError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    client.fail_assign = False
    barrier = Barrier(2)
    client.sync_time = lambda: barrier.wait(timeout=5)
    claim_barrier = Barrier(2)
    claims_done = Event()
    claims_lock = Lock()
    completed_claims = 0
    original_transition = dispatches.transition
    original_assign = client.assign_task

    def transition(*args, **kwargs):
        nonlocal completed_claims
        if kwargs.get("stage") != "assigning":
            return original_transition(*args, **kwargs)
        claim_barrier.wait(timeout=5)
        try:
            return original_transition(*args, **kwargs)
        finally:
            with claims_lock:
                completed_claims += 1
                if completed_claims == 2:
                    claims_done.set()

    def assign(**kwargs):
        assert claims_done.wait(timeout=5)
        original_assign(**kwargs)

    dispatches.transition = transition
    client.assign_task = assign

    def execute():
        try:
            return service.execute_dispatch(draft["dispatch_id"], user=admin)["status"]
        except DispatchConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sorted(results) == ["conflict", "sent"]
    assert len(client.uploads) == 1
    assert len(client.assignments) == 2  # initial rejected assignment + one retry
    assert dispatches.get(draft["dispatch_id"])["status"] == "sent"


def test_stale_upload_retry_cannot_cross_an_unknown_result(tmp_path: Path):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    raw = service.pdm.files[0]
    downloaded = service.downloads.download(document_id=raw["document_id"],
        version_id=raw["version_id"], file_path=raw["file_path"], user=admin)
    draft = service.create_dispatch(document_id=None, download_id=downloaded["download_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)

    def changed_during_sync():
        dispatches.transition(draft["dispatch_id"], expected={"downloaded"},
            status="pushing", stage="uploading", increment_attempt=True, now=service._now())
        dispatches.transition(draft["dispatch_id"], expected={"pushing"},
            status="push_failed", stage="upload_unknown", now=service._now())

    client.sync_time = changed_during_sync
    with pytest.raises(DispatchConflictError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    assert client.uploads == []
    assert dispatches.get(draft["dispatch_id"])["stage"] == "upload_unknown"


@pytest.mark.parametrize("stage,remote_id,recovered,retry_safe", [
    ("uploading", None, "upload_unknown", False),
    ("uploaded", "remote", "assign_failed", True),
    ("assigning", "remote", "assign_unknown", False),
    ("cancelling", "remote", "cancel_unknown", False),
])
def test_restart_preserves_uncertain_effects_and_only_retries_unstarted_assignment(
    tmp_path, stage, remote_id, recovered, retry_safe,
):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    arguments = dict(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    draft = service.create_dispatch(**arguments)
    dispatches.transition(draft["dispatch_id"], expected={"ready"}, status="pushing",
        stage=stage, remote_task_id=remote_id, increment_attempt=True, now=service._now())
    for _ in range(2):
        dispatches.recover_interrupted(service._now())
        assert dispatches.get(draft["dispatch_id"])["stage"] == recovered
    assert service.get_dispatch(draft["dispatch_id"])["can_execute"] is retry_safe
    with pytest.raises(DuplicateDispatchError):
        service.create_dispatch(**arguments)
    if retry_safe:
        assert service.execute_dispatch(draft["dispatch_id"], user=admin)["status"] == "sent"
        assert len(client.assignments) == 1
    else:
        with pytest.raises(DispatchConflictError):
            service.execute_dispatch(draft["dispatch_id"], user=admin)
        assert client.assignments == []
    assert client.uploads == []


def test_known_upload_rejection_retries_but_old_task_cannot_bypass_replacement(tmp_path):
    class RejectedClient(RecordingMagicClient):
        def upload_task(self, **kwargs):
            self.uploads.append(kwargs)
            raise MagicBoxError("明确拒绝上传")

    client = RejectedClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    arguments = dict(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    first = service.create_dispatch(**arguments)
    with pytest.raises(MagicBoxError):
        service.execute_dispatch(first["dispatch_id"], user=admin)
    assert service.get_dispatch(first["dispatch_id"])["can_execute"] is True
    with pytest.raises(MagicBoxError):
        service.execute_dispatch(first["dispatch_id"], user=admin)
    assert len(client.uploads) == 2  # confirmed rejection can retry the same identity
    replacement = service.create_dispatch(**arguments)
    with pytest.raises(DispatchConflictError):
        service.execute_dispatch(first["dispatch_id"], user=admin)
    assert len(client.uploads) == 2
    assert dispatches.get(replacement["dispatch_id"])["status"] == "ready"


def test_attempt_version_prevents_a_stale_retry_after_status_returns_to_same_value(tmp_path):
    service, dispatches, admin = _push_service(tmp_path, RecordingMagicClient())
    draft = service.create_dispatch(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    original = dispatches.transition(draft["dispatch_id"], expected={"ready"},
        status="push_failed", stage="upload_failed", now=service._now())
    dispatches.transition(draft["dispatch_id"], expected={"push_failed"},
        status="pushing", stage="uploading", increment_attempt=True, now=service._now())
    dispatches.transition(draft["dispatch_id"], expected={"pushing"},
        status="push_failed", stage="upload_failed", now=service._now())
    assert dispatches.transition(draft["dispatch_id"], expected={"push_failed"},
        expected_stage=original["stage"], expected_attempt_count=original["attempt_count"],
        status="pushing", stage="uploading", increment_attempt=True, now=service._now()) is None


@pytest.mark.parametrize("operation", ["upload", "assign", "cancel"])
def test_unexpected_side_effect_failure_is_fail_closed(tmp_path, operation):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    arguments = dict(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    draft = service.create_dispatch(**arguments)
    if operation == "cancel":
        service.execute_dispatch(draft["dispatch_id"], user=admin)

    def fail(**_kwargs):
        raise RuntimeError("unexpected response failure")

    setattr(client, {"upload": "upload_task", "assign": "assign_task",
        "cancel": "cancel_assignment"}[operation], fail)
    with pytest.raises(RuntimeError, match="unexpected"):
        if operation == "cancel":
            service.cancel_dispatch(draft["dispatch_id"])
        else:
            service.execute_dispatch(draft["dispatch_id"], user=admin)
    assert dispatches.get(draft["dispatch_id"])["stage"] == operation + "_unknown"
    assert service.get_dispatch(draft["dispatch_id"])["can_execute"] is False
    with pytest.raises(DuplicateDispatchError):
        service.create_dispatch(**arguments)
    if operation == "cancel":
        assert service.get_dispatch(draft["dispatch_id"])["can_cancel"] is False
        with pytest.raises(DispatchConflictError):
            service.cancel_dispatch(draft["dispatch_id"])


def test_execution_and_cancellation_share_one_atomic_claim(tmp_path):
    client = RecordingMagicClient(fail_assign=True)
    service, dispatches, admin = _push_service(tmp_path, client)
    draft = service.create_dispatch(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    with pytest.raises(MagicBoxError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    client.fail_assign = False
    barrier = Barrier(2)
    client.sync_time = lambda: barrier.wait(timeout=5)
    cancellations = []
    client.cancel_assignment = lambda **kwargs: cancellations.append(kwargs)

    def operate(cancel):
        try:
            result = (service.cancel_dispatch(draft["dispatch_id"]) if cancel else
                service.execute_dispatch(draft["dispatch_id"], user=admin))
            return result["status"]
        except DispatchConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(operate, [False, True]))
    assert results.count("conflict") == 1
    assert len(client.assignments) - 1 + len(cancellations) == 1
    assert len(client.uploads) == 1


@pytest.mark.parametrize("operation", ["assign", "cancel"])
def test_lost_remote_response_disables_retry_and_replacement(tmp_path, operation):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    arguments = dict(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    draft = service.create_dispatch(**arguments)
    if operation == "cancel":
        service.execute_dispatch(draft["dispatch_id"], user=admin)

    def lost(**_kwargs):
        raise MagicBoxError("响应丢失", outcome_unknown=True)

    setattr(client, "assign_task" if operation == "assign" else "cancel_assignment", lost)
    with pytest.raises(MagicBoxError):
        if operation == "cancel":
            service.cancel_dispatch(draft["dispatch_id"])
        else:
            service.execute_dispatch(draft["dispatch_id"], user=admin)
    assert dispatches.get(draft["dispatch_id"])["stage"] == operation + "_unknown"
    with pytest.raises(DispatchConflictError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    with pytest.raises(DuplicateDispatchError):
        service.create_dispatch(**arguments)


def test_rejected_cancellation_preserves_original_state_and_can_retry(tmp_path):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    draft = service.create_dispatch(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    service.execute_dispatch(draft["dispatch_id"], user=admin)

    def reject(**_kwargs):
        raise MagicBoxError("明确拒绝取消")

    client.cancel_assignment = reject
    with pytest.raises(MagicBoxError):
        service.cancel_dispatch(draft["dispatch_id"])
    current = dispatches.get(draft["dispatch_id"])
    assert (current["status"], current["stage"]) == ("sent", "complete")
    client.cancel_assignment = lambda **_: None
    assert service.cancel_dispatch(draft["dispatch_id"])["status"] == "cancelled"


def test_concurrent_file_preparation_failure_has_one_executor(tmp_path):
    service, dispatches, admin = _push_service(tmp_path, RecordingMagicClient())
    draft = service.create_dispatch(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    barrier = Barrier(2)
    original_get = dispatches.get

    def get(identifier):
        snapshot = original_get(identifier)
        if snapshot["status"] == "ready":
            barrier.wait(timeout=5)
        return snapshot

    dispatches.get = get
    calls = []

    def fail(**_kwargs):
        calls.append(True)
        raise DownloadError("文件来源不可用")

    service.downloads.get_or_download = fail

    def execute():
        try:
            service.execute_dispatch(draft["dispatch_id"], user=admin)
        except DispatchConflictError:
            return "conflict"
        except CuttingDispatchError:
            return "failed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: execute(), range(2)))
    assert sorted(results) == ["conflict", "failed"]
    assert len(calls) == 1
    assert original_get(draft["dispatch_id"])["attempt_count"] == 1


@pytest.mark.parametrize("failed_stage,recovered_stage", [
    ("uploaded", "upload_unknown"), ("complete", "assign_unknown"),
])
def test_remote_success_with_local_commit_failure_cannot_be_repeated(
    tmp_path, failed_stage, recovered_stage,
):
    client = RecordingMagicClient()
    service, dispatches, admin = _push_service(tmp_path, client)
    arguments = dict(document_id=service.pdm.files[0]["document_id"],
        machine_ip="10.1.133.197", material=None, thickness=None, quantity=1, user=admin)
    draft = service.create_dispatch(**arguments)
    original_transition = dispatches.transition

    def fail_commit(*args, **kwargs):
        if kwargs.get("stage") == failed_stage:
            raise RuntimeError("injected local persistence failure")
        return original_transition(*args, **kwargs)

    dispatches.transition = fail_commit
    with pytest.raises(RuntimeError, match="persistence"):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    assert len(client.uploads) == 1
    assert len(client.assignments) == (1 if failed_stage == "complete" else 0)
    with pytest.raises(DuplicateDispatchError):
        service.create_dispatch(**arguments)
    with pytest.raises(DispatchConflictError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    dispatches.transition = original_transition
    dispatches.recover_interrupted(service._now())
    assert dispatches.get(draft["dispatch_id"])["stage"] == recovered_stage
    with pytest.raises(DuplicateDispatchError):
        service.create_dispatch(**arguments)
    with pytest.raises(DispatchConflictError):
        service.execute_dispatch(draft["dispatch_id"], user=admin)
    assert len(client.uploads) == 1
