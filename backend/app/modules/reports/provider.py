"""Shared access, refresh and lookup contract for report providers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator, Protocol, TypeVar

from app.modules.reports.snapshots import ReportData

if TYPE_CHECKING:
    from app.runtime.database import DatabaseServices


Query = TypeVar('Query', contravariant=True)


class ProductionReportProvider(Protocol[Query]):
    """Queries stay line-specific; refresh plans carry JSON-safe task queries.

    source_identity is an opaque connection/scope identity, never an AI data
    version. ReportData metadata describes freshness; payload versions retain
    their existing business algorithms.
    """
    report_types: tuple[str, ...]

    def source_identity(self) -> str: ...
    def fetch_summary(self, report_type: str, query: Query, *, force: bool = False,
                      requested_at: float | None = None) -> ReportData: ...
    def fetch_detail(self, report_type: str, query: Query) -> object: ...
    def refresh_summary(self, report_type: str, query: dict, *, requested_at: float | None = None) -> None: ...
    def refresh_plan(self) -> list[tuple[dict, int]]: ...


def report_provider(context: DatabaseServices, line_id: str) -> ProductionReportProvider[Any]:
    if line_id in context.shared_reports:
        return context.shared_reports[line_id]
    if not context.catalog.get(line_id).enabled:
        raise ValueError("产线已停用")
    line = context.lines.get(line_id)
    if line is None:
        raise ValueError('产线没有登记报表服务')
    return line.report_provider


def report_providers(context: DatabaseServices) -> Iterator[tuple[str, ProductionReportProvider[Any]]]:
    for line_id, line in context.lines.items():
        if line.config.enabled:
            yield line_id, line.report_provider
    yield from context.shared_reports.items()
