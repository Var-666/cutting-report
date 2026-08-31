from __future__ import annotations

import sqlite3
from types import TracebackType

from app.database.automation import AutomationRepository
from app.database.definitions import DefinitionRepository
from app.database.dispatch import DispatchRepository
from app.database.metadata import MetadataRepository
from app.database.sqlite import SQLiteDatabase
from app.database.workshop import WorkshopRepository


class UnitOfWork:
    """Share one SQLite transaction across repository boundaries."""

    def __init__(self, database: SQLiteDatabase, timezone: str = "Asia/Shanghai") -> None:
        self.database = database
        self.timezone = timezone
        self._context = None
        self.connection: sqlite3.Connection | None = None
        self.metadata: MetadataRepository
        self.workshop: WorkshopRepository
        self.automation: AutomationRepository
        self.definitions: DefinitionRepository
        self.dispatches: DispatchRepository

    def __enter__(self) -> UnitOfWork:
        self._context = self.database.connect()
        self.connection = self._context.__enter__()
        self.connection.execute("BEGIN IMMEDIATE")
        self.metadata = MetadataRepository(
            self.database, connection=self.connection
        )
        self.workshop = WorkshopRepository(
            self.database, connection=self.connection
        )
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
