"""Build each supplied PDM table from the same filtered aggregate population."""
from collections import defaultdict
from typing import Any

from app.database.pdm_statistics import PdmStatisticsRepository
from app.models.pdm_statistics import (
    PDM_TYPES, PdmBranchOptions, PdmCounts, PdmOptionsQuery, PdmOption,
    PdmReportSummaries, PdmStatisticsQuery, PdmStatisticsReport, PdmSummaryRow, PdmSummaryTable,
)

REPORT_PROFESSIONS = ('总体', '外舾', '内装', '船体', '轮机', '电气', '船管', '工法')


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
    def __init__(self, repository: PdmStatisticsRepository) -> None:
        self.repository = repository

    def options(self, query: PdmOptionsQuery) -> PdmBranchOptions:
        fields: dict[str, list[PdmOption]] = defaultdict(list)
        for row in self.repository.options(query.kind, query.filters):
            fields[row['field']].append(PdmOption(value=row['value'], label=row['label'] or '未填写'))
        return PdmBranchOptions(**fields)

    def overview(self) -> PdmReportSummaries:
        return PdmReportSummaries(**{
            kind: PdmCounts(**counts(self.repository.overview(kind))) for kind in PDM_TYPES
        })

    def report(self, query: PdmStatisticsQuery) -> PdmStatisticsReport:
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
