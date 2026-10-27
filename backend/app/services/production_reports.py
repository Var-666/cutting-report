"""Business adapters share a small access contract, not a common set of metrics."""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol

from app.services.report_snapshots import ReportData
from app.services.production_lines import registered_lines, resolve_line_service


class ProductionReportProvider(Protocol):
    report_types: tuple[str, ...]

    def source_identity(self) -> str: ...
    def fetch_summary(self, report_type: str, query: Any, *, force: bool = False) -> ReportData: ...
    def fetch_detail(self, report_type: str, query: Any) -> Any: ...
    def refresh_summary(self, report_type: str, query: dict, *, requested_at: float | None = None) -> None: ...
    def refresh_plan(self) -> list[tuple[dict, int]]: ...


class CuttingReportProvider:
    """Keep the existing month table, bootstrap, refresh and public result unchanged."""
    report_types = ('leadership',)

    def __init__(self, leadership) -> None:
        self.leadership = leadership

    def source_identity(self) -> str:
        return self.leadership._source_fingerprint()

    def fetch_summary(self, report_type: str, query: dict, *, force: bool = False) -> ReportData:
        if report_type not in self.report_types:
            raise ValueError('切割报表类型不支持')
        month = query['report_month']
        if isinstance(month, str):
            month = date.fromisoformat(month)
        result = self.leadership.fetch(month, force=force)
        state = result.snapshot
        return ReportData(result, {'line_id': self.leadership.settings.device_id, 'report_type': report_type,
            'data_as_of': state.data_as_of.isoformat() if state.data_as_of else None,
            'source': 'snapshot' if state.cached else 'database', 'stale': state.stale, 'refreshing': state.refreshing})

    def fetch_detail(self, report_type: str, query: dict) -> Any:
        return self.fetch_summary(report_type, query).payload.daily_records

    def refresh_summary(self, report_type: str, query: dict, *, requested_at: float | None = None) -> None:
        self.fetch_summary(report_type, query, force=True)

    def refresh_plan(self) -> list[tuple[dict, int]]:
        # Existing cutting scheduling also drives bootstrap and AI; keep that path intact.
        return []


def report_provider(context, line_id: str) -> ProductionReportProvider:
    service = resolve_line_service(context, line_id)
    provider = getattr(service, 'report_provider', None)
    if provider is None:
        raise ValueError('产线没有登记报表服务')
    return provider


def report_providers(context):
    for line in registered_lines(context):
        if line['enabled']:
            service = resolve_line_service(context, line['id'])
            provider = getattr(service, 'report_provider', None)
            if provider is not None:
                yield line['id'], provider
