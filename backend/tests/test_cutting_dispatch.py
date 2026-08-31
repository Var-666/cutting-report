from dataclasses import replace
from pathlib import Path

import pytest

from app.config import settings
from app.database.dispatch import DispatchRepository
from app.database.pdm import MockPdmRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import WorkshopRepository
from app.services.cutting_dispatch_service import (
    CuttingDispatchService,
    DuplicateDispatchError,
)
from app.services.file_source import (
    DisabledFileSource,
    LocalFileSource,
    MockFileSource,
    normalize_relative_path,
)


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
