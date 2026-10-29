from __future__ import annotations

import sqlite3
from types import TracebackType

from app.config import DEVICE_CATALOG
from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.metadata import MetadataRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop_reports import WorkshopReportRepository
from app.database.confirmations import ConfirmationRepository
from app.database.users import UserRepository
from app.database.sessions import SessionRepository


class UnitOfWork:
    """Share one SQLite transaction across repository boundaries."""

    def __init__(self, database: SQLiteDatabase, timezone: str = "Asia/Shanghai", *, device_id: str | None = None) -> None:
        self.database = database
        self.timezone = timezone
        self.device_id = device_id
        self.scope_machine_no = None
        if device_id is not None:
            device = next((item for item in DEVICE_CATALOG.devices if item.id == device_id), None)
            if device is None:
                raise ValueError("设备不存在")
            self.scope_machine_no = device.machine_no
        self._context = None
        self.connection: sqlite3.Connection | None = None
        self.metadata: MetadataRepository
        self.workshop: WorkshopReportRepository
        self.confirmations: ConfirmationRepository
        self.users: UserRepository
        self.sessions: SessionRepository
        self.automation: AutomationRepository
        self.definitions: DefinitionRepository
        self.dispatches: DispatchRepository

    def __enter__(self) -> UnitOfWork:
        self._context = self.database.connect()
        self.connection = self._context.__enter__()
        self.connection.execute("BEGIN IMMEDIATE")
        self.metadata = MetadataRepository(
            self.database, connection=self.connection, device_id=self.device_id
        )
        self.workshop = WorkshopReportRepository(
            self.database, connection=self.connection, scope_machine_no=self.scope_machine_no
        )
        self.confirmations = ConfirmationRepository(
            self.database, connection=self.connection, scope_machine_no=self.scope_machine_no
        )
        self.users = UserRepository(self.database, connection=self.connection)
        self.sessions = SessionRepository(self.database, connection=self.connection)
        self.automation = AutomationRepository(
            self.database, connection=self.connection
        )
        self.definitions = DefinitionRepository(
            self.database, self.timezone, connection=self.connection
        )
        self.dispatches = DispatchRepository(
            self.database, connection=self.connection
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if self._context is None:
            return None
        return self._context.__exit__(exc_type, exc_value, traceback)
