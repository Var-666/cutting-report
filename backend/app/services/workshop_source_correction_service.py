from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.workshop import WorkshopRepository
from app.models.domain import ReportFilter
from app.services.query_service import QueryService


class WorkshopSourceCorrectionService:
    """Read the external source before a short, reviewed SQLite correction."""

    def __init__(self, settings: Settings, repository: WorkshopRepository, query: QueryService):
        self.settings = settings
        self.repository = repository
        self.query = query

    def _arguments(self, report_date: date) -> dict:
        bundle = self.query.fetch_workshop_data(ReportFilter(report_date=report_date), force=True)
        return {
            "report_date": report_date.isoformat(),
            "source_mode": self.settings.effective_data_source_mode,
            "scope_machine_no": self.settings.report_machine_no,
            "records": bundle.workshop_records,
        }

    def preview(self, report_date: date) -> dict:
        return self.repository.preview_source_corrections(**self._arguments(report_date))

    def apply(self, report_date: date, *, expected_version: str, reason: str, admin: dict) -> dict:
        arguments = self._arguments(report_date)
        result = self.repository.apply_source_corrections(
            **arguments, expected_version=expected_version, reason=reason, admin=admin,
            now=datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds"),
        )
        self.query.invalidate_workshop_data()
        return result
