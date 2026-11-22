from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from time import time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from app.models.domain import LeadershipReportResult, QueryBundle, ReportFilter, to_primitive
from app.database.report_snapshots import ReportKey, fingerprint
from app.modules.cutting.equipment import summarize_equipment
from app.modules.cutting.leadership import LeadershipReportService
from app.modules.cutting.range_reports import validate_report_range
from app.services.report_snapshots import ReportData, ReportSnapshotService
from app.services.report_cache_policy import report_refresh_seconds


class CuttingReportProvider:
    """Keep the existing month table, bootstrap, refresh and public result unchanged."""
    report_types = ('leadership',)

    def __init__(self, leadership: LeadershipReportService, *, pipeline=None, snapshots=None, coordination=None,
                 source_identity: str | None = None) -> None:
        self.leadership = leadership
        self.settings = leadership.settings
        self.pipeline = pipeline
        self._source = source_identity
        self.snapshot_service = ReportSnapshotService(self.settings, snapshots, coordination) if snapshots else None
        if self.snapshot_service:
            self.report_types = ('leadership', 'leadership-range', 'equipment', 'workshop:source',
                'energy:day', 'energy:tasks', 'energy:range', 'energy:month', 'energy:monthly-range')
            self.pipeline.energy_period_service.on_snapshot_saved = self.invalidate_energy
            self.leadership.on_snapshot_saved = self.invalidate_leadership

    def invalidate_energy(self, start: date, end: date) -> None:
        self.snapshot_service.repository.invalidate(self.settings.device_id, self.source_identity(),
            ('equipment', 'energy:day', 'energy:tasks', 'energy:range', 'energy:month', 'energy:monthly-range'),
            start=start.isoformat(), end=end.isoformat())
        self.snapshot_service.revision.publish()

    def invalidate_leadership(self) -> None:
        self.snapshot_service.repository.invalidate(self.settings.device_id, self.source_identity(), ('leadership', 'leadership-range'))
        self.snapshot_service.revision.publish()

    def source_identity(self) -> str:
        if self._source is None:
            return self.leadership.source_identity()
        return fingerprint((self.leadership.source_identity(), self._source,
                            self.pipeline.energy_period_service.history_source_key, 1))

    def _query(self, report_type: str, values: dict) -> dict:
        query = dict(values.get('payload', values))
        if report_type == 'leadership' or report_type == 'energy:month':
            month = query['report_month']
            if isinstance(month, str):
                month = date.fromisoformat(month + '-01' if len(month) == 7 else month)
            month = month.replace(day=1)
            query = {'report_month': month.isoformat(), 'start': month.isoformat(),
                     'end': month.replace(day=monthrange(month.year, month.month)[1]).isoformat()}
        else:
            start = query.get('start', query.get('report_date'))
            end = query.get('end', start)
            start = date.fromisoformat(start) if isinstance(start, str) else start
            end = date.fromisoformat(end) if isinstance(end, str) else end
            validate_report_range(start, end)
            query.update(start=start.isoformat(), end=end.isoformat())
            query.pop('report_date', None)
        return query

    def _snapshot_key(self, report_type: str, query: dict) -> ReportKey:
        return ReportKey(self.settings.device_id, report_type, query['start'], query['end'],
            {'payload': query}, self.source_identity())

    def _load_page(self, report_type: str, query: dict, requested_at: float | None) -> dict:
        start, end = date.fromisoformat(query['start']), date.fromisoformat(query['end'])
        if report_type == 'leadership':
            result = self.leadership.fetch(start)
            if result.snapshot.stale or result.snapshot.data_as_of is None or requested_at is None or result.snapshot.data_as_of.timestamp() < requested_at:
                result = self.leadership.fetch(start, force=True)
            return to_primitive(result)
        ranges = self.pipeline.range_service
        energy = self.pipeline.energy_period_service
        if report_type == 'leadership-range':
            return to_primitive(ranges.leadership(start, end, force=True))
        if report_type == 'workshop:source':
            bundle = ranges.workshop_bundle(start, end, force=True)
            return TypeAdapter(QueryBundle).dump_python(bundle, mode='json')
        if report_type == 'equipment':
            result = self.pipeline.query_service.fetch_equipment_data(ReportFilter(report_date=start), force=True)
            enriched = self.pipeline.energy_service.enrich(result.records, force=True)
            if enriched.source_failed:
                raise ConnectionError('能耗取数失败，保留原统计结果')
            rows = enriched.records
            return to_primitive({'report_date': start.isoformat(), 'records': rows[:self.settings.report_max_detail_rows],
                'summary': summarize_equipment(rows, enriched.mode), 'data_updated_at': result.data_updated_at,
                'energy_notice': enriched.notice, 'units': enriched.units, 'truncated': len(rows) > self.settings.report_max_detail_rows})
        if report_type == 'energy:day':
            result = energy.get_day(start, force=True)
            if result.get('sync_ok') is False and not energy.configuration_error():
                raise ConnectionError('能耗取数失败，保留原统计结果')
            return result
        if report_type == 'energy:tasks':
            source = self.pipeline.query_service.fetch_equipment_data(ReportFilter(report_date=start), force=True)
            result = energy.get_task_day(start, source.records, force=True)
            if result.get('source_failed'):
                raise ConnectionError('能耗取数失败，保留原统计结果')
            result['data_updated_at'] = to_primitive(source.data_updated_at)
            return result
        if report_type == 'energy:range':
            result = ranges.equipment(start, end, force=True)
            if result['task_report'].get('source_failed'):
                raise ConnectionError('能耗取数失败，保留原统计结果')
            return to_primitive(result)
        if report_type == 'energy:month':
            return energy.get_month(start.strftime('%Y-%m'), force=True)
        if report_type == 'energy:monthly-range':
            return energy.get_monthly_range(start, end, force=True)
        raise ValueError('切割统计类型不支持')

    def page(self, report_type: str, query: dict, *, force: bool = False) -> dict:
        result = self.fetch_summary(report_type, query, force=force)
        return {**to_primitive(result.payload), 'meta': {'report_data': result.meta}}

    def workshop_source(self, start: date, end: date, *, force: bool = False, requested_at: float | None = None) -> tuple[QueryBundle, dict]:
        result = self.fetch_summary('workshop:source', {'start': start, 'end': end}, force=force, requested_at=requested_at)
        return TypeAdapter(QueryBundle).validate_python(result.payload), result.meta

    def fetch_summary(self, report_type: str, query: dict, *, force: bool = False,
                      requested_at: float | None = None) -> ReportData:
        if report_type not in self.report_types:
            raise ValueError('切割报表类型不支持')
        if self.snapshot_service:
            values = self._query(report_type, query)
            data = self.snapshot_service.read(self._snapshot_key(report_type, values),
                lambda: self._load_page(report_type, values, requested_at), force=force,
                requested_at=requested_at, background=requested_at is not None, background_only=True,
                expire_at_midnight=report_type.startswith('energy:'))
            if report_type == 'leadership':
                data.payload = TypeAdapter(LeadershipReportResult).validate_python(data.payload)
            return data
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
        if self.snapshot_service and requested_at is None:
            requested_at = time()
        self.fetch_summary(report_type, query, force=True, requested_at=requested_at)

    def refresh_plan(self) -> list[tuple[dict, int]]:
        if self.snapshot_service is None:
            return []
        today = datetime.now(ZoneInfo(self.settings.timezone)).date()
        queries = [('leadership', {'report_month': today}), ('workshop:source', {'report_date': today}),
                   ('equipment', {'report_date': today})]
        if self.settings.energy_connected:
            queries.insert(1, ('energy:day', {'report_date': today}))
            queries += [('energy:tasks', {'report_date': today}),
                        ('energy:month', {'report_month': today})]
        return [(self.snapshot_service.task_payload(self._snapshot_key(kind, self._query(kind, query))),
                 report_refresh_seconds(self.settings)) for kind, query in queries]
