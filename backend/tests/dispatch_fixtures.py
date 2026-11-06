"""Shared dispatch setup; every caller supplies its isolated state database."""
from pathlib import Path

from tests.line_fixtures import cutting_settings
from app.database.dispatch import DispatchRepository
from app.database.downloads import DownloadRepository
from app.database.pdm import MockPdmRepository
from app.database.workshop import WorkshopRepository
from app.services.cutting_dispatch_service import CuttingDispatchService
from app.services.file_download_service import FileDownloadService
from app.services.file_source import DisabledFileSource, MockFileSource
from app.services.magic_box_service import MagicBoxConfig, MagicBoxError


def dispatch_service(state_database, tmp_path: Path, file_source=None):
    configured = cutting_settings(project_root=tmp_path)
    database = state_database
    workshop = WorkshopRepository(database)
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


def push_service(state_database, tmp_path: Path, client: RecordingMagicClient):
    cfg = cutting_settings(project_root=tmp_path, magic_box_enabled=True)
    database = state_database
    workshop = WorkshopRepository(database)
    dispatches = DispatchRepository(database)
    dispatches.initialize()
    download_repository = DownloadRepository(database)
    pdm = MockPdmRepository()
    pdm.mode = "sqlserver"
    for index, file in enumerate(pdm.files):
        file["document_no"] = f"Z{806 + index}C080A0{index + 1:02d}.GEN"
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
