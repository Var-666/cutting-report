"""Enrich task display metadata without changing task identity or production totals."""
from dataclasses import replace
import logging
from typing import TypeVar

from app.core.request_cancellation import SqlRequestCancelled
from app.core.report_deadline import ReportOperationTimeout
from app.database.bm_projects import BmProjectRepository
from app.services.query_cache import QueryCache

logger = logging.getLogger(__name__)
T = TypeVar('T')


class ShipNameService:
    def __init__(self, repository: BmProjectRepository, *, cache_seconds: int) -> None:
        self.repository = repository
        self.cache_seconds = cache_seconds
        self._cache = QueryCache[dict[str, str | None]]()

    def names_for(self, project_numbers: list[str], *, force: bool = False) -> dict[str, str | None] | None:
        numbers = tuple(sorted({number.strip().upper() for number in project_numbers if number and number.strip()}))
        if not numbers:
            return {}
        try:
            return self._cache.read(numbers, lambda: self.repository.ship_names(numbers),
                ttl=self.cache_seconds, force=force)
        except (SqlRequestCancelled, ReportOperationTimeout):
            raise
        except Exception as exc:
            # External driver text can contain credentials; keep production and confirmations usable.
            logger.warning('BM ship name lookup unavailable type=%s', type(exc).__name__)
            return None

    def enrich(self, records: list[T], *, force: bool = False) -> list[T]:
        names = self.names_for([row.project_code for row in records], force=force)
        if names is None:
            return [replace(row, ship_no=row.ship_no if row.ship_no != row.project_code else None) for row in records]
        return [replace(row, ship_no=names.get((row.project_code or '').strip().upper())) for row in records]
