from __future__ import annotations

from types import TracebackType
from psycopg import Connection

from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.metadata import MetadataRepository
from app.database.postgres import PostgresDatabase
from app.database.workshop_reports import WorkshopReportRepository
from app.database.confirmations import ConfirmationRepository
from app.database.users import UserRepository
from app.database.sessions import SessionRepository


class UnitOfWork:
    """One pooled PostgreSQL transaction, shared by each bound repository."""

    def __init__(self, database: PostgresDatabase, timezone: str = 'Asia/Shanghai', *, device_id: str | None = None, scope_machine_no: str | None = None) -> None:
        self.database = database
        self.timezone = timezone
        self.device_id = device_id
        self.scope_machine_no = scope_machine_no
        if device_id is not None and not scope_machine_no:
            raise ValueError('必须显式指定产线的机床范围')
        self._context = None
        self.connection: Connection | None = None

    def __enter__(self) -> UnitOfWork:
        self._context = self.database.connect()
        self.connection = self._context.__enter__()
        self.metadata = MetadataRepository(self.database, connection=self.connection, device_id=self.device_id)
        self.workshop = WorkshopReportRepository(self.database, connection=self.connection, scope_machine_no=self.scope_machine_no)
        self.confirmations = ConfirmationRepository(self.database, connection=self.connection, scope_machine_no=self.scope_machine_no)
        self.users = UserRepository(self.database, connection=self.connection)
        self.sessions = SessionRepository(self.database, connection=self.connection)
        self.automation = AutomationRepository(self.database, connection=self.connection)
        self.definitions = DefinitionRepository(self.database, self.timezone, connection=self.connection)
        self.dispatches = DispatchRepository(self.database, connection=self.connection)
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> bool | None:
        return self._context.__exit__(exc_type, exc_value, traceback)
