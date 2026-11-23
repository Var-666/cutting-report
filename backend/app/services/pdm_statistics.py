"""Build each supplied PDM table from the same filtered aggregate population."""
from collections import defaultdict
from calendar import monthrange
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.pdm_statistics import PdmStatisticsRepository
from app.modules.reports.snapshot_repository import ReportKey, fingerprint
from app.modules.reports.snapshots import ReportData, ReportSnapshotService
from app.modules.reports.cache_policy import report_refresh_seconds
from app.models.pdm_statistics import (
    PDM_TYPES, PdmBranchOptions, PdmCounts, PdmOptionsQuery, PdmOption,
    PdmReportSummaries, PdmOverviewReport, PdmStatisticsQuery, PdmStatisticsReport, PdmSummaryRow, PdmSummaryTable,
)

REPORT_PROFESSIONS = ('总体', '外舾', '内装', '船体', '轮机', '电气', '船管', '工法')
PDM_STATISTICS_SCOPE = '@pdm-statistics'


def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(int(row['total']) for row in rows)
    completed = sum(int(row['completed']) for row in rows)
    on_time = sum(int(row['on_time']) for row in rows)
    return dict(total=total, completed=completed, on_time=on_time,
                total_rate=round(completed / total * 100, 2) if total else None,
                on_time_rate=round(on_time / total * 100, 2) if total else None)


def groups(pairs: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    if field == 'profession':
        names = {pair['profession_name'] for pair in pairs}
        return [dict(profession_id=name, profession_name=name) for name in REPORT_PROFESSIONS if name in names]
    unique = {}
    for pair in pairs:
        if pair['project_id'] and pair['project_name'].strip() and pair['project_name'] != '未填写':
            unique.setdefault(pair['project_id'], pair)
    return sorted(unique.values(), key=lambda row: (row['ship_no'], row['project_name'], row['project_id'] or ''))


def make_table(kind: str, records: list[dict[str, Any]], pairs: list[dict[str, Any]],
               *, key: str, scope: str, group: str | None = None) -> PdmSummaryTable:
    subdivisions = ([{'profession_name': name} for name in REPORT_PROFESSIONS]
                    if group == 'profession' else groups(pairs, group) if group else [])
    rows = []
    for _, source_name, table_label in PDM_TYPES[kind].values():
        category_rows = [row for row in records if row['type_name'] == source_name]
        if group:
            for subdivision in subdivisions:
                # The document fixes profession rows by name, across project-specific IDs.
                field = f'{group}_name' if group == 'profession' else f'{group}_id'
                ident = subdivision[field] or ''
                matched = [row for row in category_rows if (row[field] or '') == ident]
                rows.append(PdmSummaryRow(category=table_label, group_id=ident,
                    group_name=subdivision[f'{group}_name'], **counts(matched)))
            rows.append(PdmSummaryRow(category=table_label, group_name='总计', subtotal=True, **counts(category_rows)))
        else:
            rows.append(PdmSummaryRow(category=table_label, **counts(category_rows)))
    return PdmSummaryTable(key=key, kind=kind, title='设备资料计划完成汇总表' if kind == 'equipment' else '详设图纸计划完成汇总表',
        scope_label=scope, group_label={'project': '项目', 'profession': '专业'}.get(group),
        rows=rows, totals=PdmCounts(**counts(records)))


class PdmStatisticsService:
    report_types = ('overview', 'report', 'options')

    def __init__(self, repository: PdmStatisticsRepository, *, settings: Settings | None = None,
                 snapshots=None, coordination=None, source_identity: str = '') -> None:
        self.repository = repository
        self.settings = replace(settings, device_id=PDM_STATISTICS_SCOPE) if settings else None
        self._source = fingerprint((source_identity, PDM_TYPES, settings.timezone if settings else '', 1))
        self.snapshot_service = ReportSnapshotService(self.settings, snapshots, coordination) if snapshots and settings else None

    def source_identity(self) -> str:
        return self._source

    @staticmethod
    def _query(report_type: str, values: dict) -> dict:
        if report_type == 'overview':
            return {}
        model = PdmStatisticsQuery if report_type == 'report' else PdmOptionsQuery
        validated = model.model_validate(values).model_dump(mode='json')
        if report_type == 'report':
            for kind in PDM_TYPES:
                if kind not in validated['document_kinds']:
                    validated[kind] = {}
        else:
            validated['filters'] = {key: validated['filters'][key] for key in ('types', 'projects', 'professions')}
        def normalize(value):
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return sorted(set(value))
            return value
        return normalize(validated)

    def _snapshot_key(self, report_type: str, values: dict) -> ReportKey:
        return ReportKey(PDM_STATISTICS_SCOPE, report_type, values.get('start_date', ''),
            values.get('end_date', ''), {'payload': values}, self.source_identity())

    def fetch_summary(self, report_type: str, query: dict, *, force: bool = False,
                      requested_at: float | None = None) -> ReportData:
        if report_type not in self.report_types:
            raise ValueError('PDM统计类型不支持')
        values = self._query(report_type, query.get('payload', query))
        def load():
            if report_type == 'overview':
                result = self._overview()
            elif report_type == 'options':
                result = self._options(PdmOptionsQuery.model_validate(values))
            else:
                result = self._report(PdmStatisticsQuery.model_validate(values))
            return result.model_dump(mode='json', exclude={'meta'})
        if self.snapshot_service is None:
            raise ValueError('PDM统计快照未配置')
        return self.snapshot_service.read(self._snapshot_key(report_type, values), load, force=force,
            requested_at=requested_at, background=requested_at is not None, background_only=True)

    def fetch_detail(self, report_type: str, query: dict):
        return self.fetch_summary(report_type, query).payload

    def refresh_summary(self, report_type: str, query: dict, *, requested_at: float | None = None) -> None:
        from time import time
        self.fetch_summary(report_type, query, force=True, requested_at=time() if requested_at is None else requested_at)

    def refresh_plan(self) -> list[tuple[dict, int]]:
        if self.snapshot_service is None or self.settings is None:
            return []
        month = datetime.now(ZoneInfo(self.settings.timezone)).date().replace(day=1)
        query = PdmStatisticsQuery(document_kinds=['equipment', 'drawing'], start_date=month,
            end_date=month.replace(day=monthrange(month.year, month.month)[1])).model_dump(mode='json')
        queries = [('overview', {}), ('report', query)]
        return [(self.snapshot_service.task_payload(self._snapshot_key(kind, self._query(kind, values))),
                 report_refresh_seconds(self.settings)) for kind, values in queries]

    def _cached(self, kind: str, values: dict, *, force: bool):
        data = self.fetch_summary(kind, values, force=force)
        return {**data.payload, 'meta': {'report_data': data.meta}}

    def options(self, query: PdmOptionsQuery, *, force: bool = False) -> PdmBranchOptions:
        if self.snapshot_service:
            return PdmBranchOptions.model_validate(self._cached('options', query.model_dump(mode='json'), force=force))
        return self._options(query)

    def _options(self, query: PdmOptionsQuery) -> PdmBranchOptions:
        fields: dict[str, list[PdmOption]] = defaultdict(list)
        for row in self.repository.options(query.kind, query.filters):
            fields[row['field']].append(PdmOption(value=row['value'], label=row['label'] or '未填写'))
        return PdmBranchOptions(**fields)

    def overview(self, *, force: bool = False) -> PdmOverviewReport:
        if self.snapshot_service:
            return PdmOverviewReport.model_validate(self._cached('overview', {}, force=force))
        return PdmOverviewReport.model_validate(self._overview().model_dump())

    def _overview(self) -> PdmReportSummaries:
        return PdmReportSummaries(**{
            kind: PdmCounts(**counts(self.repository.overview(kind))) for kind in PDM_TYPES
        })

    def report(self, query: PdmStatisticsQuery, *, force: bool = False) -> PdmStatisticsReport:
        if self.snapshot_service:
            return PdmStatisticsReport.model_validate(self._cached('report', query.model_dump(mode='json'), force=force))
        return self._report(query)

    def _report(self, query: PdmStatisticsQuery) -> PdmStatisticsReport:
        tables, expanded = [], []
        summaries = {}
        for kind in query.document_kinds:
            filters = getattr(query, kind)
            records = self.repository.summary(kind, filters, query)
            pairs = self.repository.group_pairs(kind, filters)
            summaries[kind] = PdmCounts(**counts(records))
            if query.group_by == 'summary':
                projects, professions = groups(pairs, 'project'), groups(pairs, 'profession')
                scope = projects[0]['project_name'] if len(filters.projects) == 1 and projects else '所选项目' if filters.projects else '所有项目'
                if len(filters.professions) == 1 and professions:
                    scope += ' / ' + professions[0]['profession_name']
                tables.append(make_table(kind, records, pairs, key=f'{kind}:summary', scope=scope))
                if kind == 'equipment':
                    for group, label in [('profession', '专业'), ('project', '项目')]:
                        expanded.append(make_table(kind, records, pairs,
                            key=f'{kind}:all:{group}', scope=f'{scope}－分{label}', group=group))
            else:
                outer = query.group_by
                inner = 'profession' if outer == 'project' else 'project'
                for partition in groups(pairs, outer):
                    field = 'profession_name' if outer == 'profession' else 'project_id'
                    ident = partition[field]
                    subset = [row for row in records if (row[field] or '') == ident]
                    scoped_pairs = [row for row in pairs if (row[field] or '') == ident]
                    tables.append(make_table(kind, subset, scoped_pairs,
                        key=f'{kind}:{outer}:{ident}', scope=partition[f'{outer}_name'], group=inner))
        return PdmStatisticsReport(start_date=query.start_date, end_date=query.end_date, date_basis=query.date_basis, group_by=query.group_by,
            summaries=PdmReportSummaries(**summaries), tables=tables + expanded)
