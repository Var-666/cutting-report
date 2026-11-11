"""Display values for archived analysis cards, using only report data."""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

from app.services.report_analysis.common import _change, _duration, _number


LEADERSHIP_FIELDS = {
    'F001': ('task_count', 'tasks'), 'F002': ('cut_length_m', 'ruler'),
    'F003': ('work_time_seconds', 'clock'), 'F007': ('groove_length_m', 'ruler'),
}
CUTTING_ICONS = {
    'workshop': {'F001': 'tasks', 'F002': 'ruler', 'F003': 'clock', 'F004': 'check'},
    'equipment': {'F001': 'tasks', 'F002': 'clock', 'F003': 'wind', 'F004': 'zap'},
}
WELDING_FIELDS = (
    ('job_count', '焊接作业次数', '次', 'tasks'),
    ('weld_length_m', '焊接总长度', 'm', 'ruler'),
    ('involved_part_count', '参与焊接工件数', '件', 'grid'),
    ('working_hours', '累计作业用时', 'h', 'clock'),
)


@lru_cache(maxsize=4)
def analysis_cover(project_root: Path) -> str:
    source = project_root / 'frontend/src/assets/home-laser-cutting.webp'
    if not source.is_file():
        source = next(iter(sorted((project_root / 'frontend/dist/assets').glob('home-laser-cutting-*.webp'))), source)
    try:
        return 'data:image/webp;base64,' + base64.b64encode(source.read_bytes()).decode('ascii')
    except OSError:
        return ''


def _sparkline(rows: list[dict], field: str) -> list[tuple[float, float]]:
    samples = [row.get(field) for row in rows[-12:]]
    if len(samples) < 2 or any(value is None for value in samples):
        return []
    values = [float(value) for value in samples]
    low, high = min(values), max(values)
    return [(round(2 + index * 62 / (len(values) - 1), 2),
             round(21 - (value - low) * 18 / (high - low), 2) if high != low else 12)
            for index, value in enumerate(values)]


def analysis_metrics(analysis: dict, kind: str, report: dict) -> list[dict]:
    if kind == 'welding':
        rows = sorted(report.get('daily', []), key=lambda row: row['production_date'])
        metrics = []
        for key, label, unit, icon in WELDING_FIELDS:
            value = report['summary'].get(key)
            display = f'{float(value):,.2f}'.rstrip('0').rstrip('.') if value is not None else '—'
            metrics.append(dict(label=label, value=f'{display} {unit}' if value is not None else display,
                                icon=icon, points=_sparkline(rows, key)))
        return metrics
    facts = analysis.get('facts') or []
    brief = analysis.get('brief')
    if not brief:
        return facts
    fact_by_id = {fact.get('id'): fact for fact in facts}
    icons = CUTTING_ICONS.get(kind, {})
    primary = LEADERSHIP_FIELDS if kind == 'leadership' else icons
    ids = primary if any(identifier in fact_by_id for identifier in primary) else brief['key_metric_ids']
    leadership = report.get('leadership') or {}
    rows = sorted(leadership.get('daily_records', []), key=lambda row: row['period_start'])
    days = sum(row.get('task_count', 0) > 0 for row in rows)
    scope = brief['scope']
    current = leadership.get('current_month_total', {})
    comparison = leadership.get('comparison_total') or {}
    comparable = scope.get('comparison_start') and scope['comparison_start'] == leadership.get('comparison_start') \
        and scope.get('comparison_end') == leadership.get('comparison_end')
    metrics = []
    for identifier in ids:
        fact = fact_by_id.get(identifier)
        if fact is None:
            continue
        field, icon = LEADERSHIP_FIELDS.get(identifier, ('', 'chart')) if kind == 'leadership' else ('', icons.get(identifier, 'chart'))
        metric = {**fact, 'icon': icon, 'points': _sparkline(rows, field) if field else []}
        if field in ('task_count', 'cut_length_m') and comparable and current.get(field) is not None:
            change = _change(current.get(field), comparison.get(field))
            if change is not None:
                metric.update(note='较上月' if scope.get('period_mode') == 'month' else '较对比周期',
                              comparison=f'{change:+.1f}%' if change else '0.0%',
                              direction='down' if change < 0 else 'up' if change > 0 else '')
        if days and field in ('work_time_seconds', 'groove_length_m') and current.get(field) is not None:
            average = current[field] / days
            metric.update(note='有记录日均',
                          comparison=_duration(average) if field == 'work_time_seconds' else f'{_number(average)} 米')
        metrics.append(metric)
    return metrics
