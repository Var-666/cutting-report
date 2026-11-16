"""Build each supplied PDM table from the same filtered aggregate population."""
from collections import defaultdict
from typing import Any

from app.database.pdm_statistics import PdmStatisticsRepository
from app.models.pdm_statistics import (
    PDM_TYPES, PdmBranchFilters, PdmBranchOptions, PdmCounts, PdmOptionsQuery, PdmOption,
    PdmReportSummaries, PdmStatisticsQuery, PdmStatisticsReport, PdmSummaryRow, PdmSummaryTable,
)


def counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(int(row['total']) for row in rows)
    completed = sum(int(row['completed']) for row in rows)
    on_time = sum(int(row['on_time']) for row in rows)
    return dict(total=total, completed=completed, on_time=on_time,
                total_rate=round(completed / total * 100, 2) if total else None,
                on_time_rate=round(on_time / total * 100, 2) if total else None)


def groups(pairs: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    unique = {}
    for pair in pairs:
        unique.setdefault(pair[f'{field}_id'] or '', pair)
    if field == 'profession':
        order = {name: index for index, name in enumerate(['总体', '外舾', '内装', '船体', '轮机', '电气', '船管', '工法'])}
        return sorted(unique.values(), key=lambda row: (order.get(row['profession_name'], len(order)), row['profession_name'], row['profession_id'] or ''))
    return sorted(unique.values(), key=lambda row: (row['ship_no'], row['project_name'], row['project_id'] or ''))


def make_table(kind: str, filters: PdmBranchFilters, records: list[dict[str, Any]], pairs: list[dict[str, Any]],
               *, key: str, scope: str, group: str | None = None) -> PdmSummaryTable:
    subdivisions = groups(pairs, group) if group else []
    rows = []
    for label, (_, source_name, table_label) in PDM_TYPES[kind].items():
        if filters.types and label not in filters.types:
            continue
        category_rows = [row for row in records if row['type_name'] == source_name]
        if group:
            for subdivision in subdivisions:
                ident = subdivision[f'{group}_id'] or ''
                matched = [row for row in category_rows if (row[f'{group}_id'] or '') == ident]
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
                tables.append(make_table(kind, filters, records, pairs, key=f'{kind}:summary', scope=scope))
                if kind == 'equipment':
                    for group, label in [('profession', '专业'), ('project', '项目')]:
                        expanded.append(make_table(kind, filters, records, pairs,
                            key=f'{kind}:all:{group}', scope=f'{scope}－分{label}', group=group))
            else:
                outer = query.group_by
                inner = 'profession' if outer == 'project' else 'project'
                for partition in groups(pairs, outer):
                    ident = partition[f'{outer}_id'] or ''
                    subset = [row for row in records if (row[f'{outer}_id'] or '') == ident]
                    scoped_pairs = [row for row in pairs if (row[f'{outer}_id'] or '') == ident]
                    tables.append(make_table(kind, filters, subset, scoped_pairs,
                        key=f'{kind}:{outer}:{ident}', scope=partition[f'{outer}_name'], group=inner))
        return PdmStatisticsReport(start_date=query.start_date, end_date=query.end_date, group_by=query.group_by,
            summaries=PdmReportSummaries(**summaries), tables=tables + expanded)
