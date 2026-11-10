"""Assemble candidates from report contexts, keeping full links out of model input."""
from __future__ import annotations

from calendar import monthrange
from collections import Counter
from datetime import date

from app.models.domain import production_day
from app.services.report_analysis.common import _fact


def period_scope(kind: str, label: str) -> dict:
    dates = label.split(' 至 ')
    if len(label) == 7:
        start = date.fromisoformat(label + '-01')
        end = start.replace(day=monthrange(start.year, start.month)[1])
        mode = 'month'
    else:
        start, end = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
        mode = 'day' if start == end else 'range'
    return dict(report_type=kind, period_start=start.isoformat(), period_end=end.isoformat(), period_mode=mode)


def add_candidates(context: dict, kind: str, records: list | None = None) -> dict:
    scope = period_scope(kind, context.get('report_month') or context['report_date'])
    comparison = context.get('comparisons', {})
    label = comparison.get('comparison_date')
    if label:
        dates = label.split(' 至 ')
        scope.update(comparison_start=dates[0], comparison_end=dates[-1])
    context['scope'] = scope
    context.setdefault('distribution_coverage', {}).update(project_note='工程及设备分布按任务数降序，最多展示前10组。')
    quality = context.setdefault('data_quality', [])
    quality.append('仅反映实际生产记录；无计划、订单、故障和设备开机时长数据，不据此判断交付、人员效率或设备利用率。')
    candidates, links = [], {}
    context['candidates'], context['_references'] = candidates, links

    def add(identifier, category, title, evidence, references=(), boundary='', priority=1):
        unique = {(ref['kind'], ref['id']): ref for ref in references}
        references = list(unique.values())
        candidates.append(dict(id=identifier, category=category, title=title, evidence_ids=evidence,
            priority=priority, boundary=boundary, reference_count=len(references),
            reference_sample=[dict(kind=r['kind'], id=r['id'], label=r['label']) for r in references[:3]],
            sample_coverage='业务对象仅预览前3项；完整关联由后端保留。'))
        links[identifier] = references

    def ref(kind, identifier, **extra):
        return dict(kind=kind, id=str(identifier), label=str(identifier), **extra)

    def task(row):
        stamp = production_day(row)
        return ref('task', row.task_id, production_date=stamp.isoformat() if stamp else None,
            project_code=row.project_code, program_no=getattr(row, 'program_no', None),
            work_time=float(row.work_time) if row.work_time is not None else None,
            confirmation=('已确认' if row.confirmed_by else '待确认') if kind == 'workshop' else None,
            energy_status=f"气耗：{row.gas_data_status}；电耗：{row.electricity_data_status}" if kind == 'equipment' else None)

    count = context.get('task_count')
    if kind == 'leadership':
        count = context['current_task_count']
        if scope.get('comparison_start'):
            add('C_comparison', 'comparison', '与上一可比周期的生产规模变化', ['F001', 'F002', 'F003', 'F004', 'F007', 'F008'],
                boundary='变化不代表订单、产能或效率变化；以明确比较日期为准。')
        if context['daily_sample_count']:
            add('C_daily', 'distribution', '周期内每日任务分布', ['F001', 'F006'],
                [ref('date', r['date']) for r in context['anomalies']],
                boundary='任务较多的日期是分布特征，不是设备告警。')
        projects = context['project_distribution']
        if projects:
            top = projects[0]
            if top['project_name'] == '未标注工程':
                quality.append('部分任务工程号未填写；缺失工程号不能解释为工程集中。')
                add('C_project_missing', 'data_quality', '工程信息缺口', ['F006'], boundary=quality[-1], priority=2)
            else:
                add('C_project', 'distribution', '主要工程的任务分布', ['F006'], [ref('project', top['project_name'])])
    else:
        records = records or []
        if records:
            dates = Counter(production_day(r).isoformat() for r in records if production_day(r))
            context['daily_distribution'] = [dict(date=stamp, task_count=count) for stamp, count in sorted(dates.items())]
            add('C_production', 'production', '本期实际切割记录', ['F001', 'F002', 'F003'] if kind == 'workshop' else ['F001', 'F002'],
                [ref('date', production_day(r).isoformat()) for r in records if production_day(r)])
        if comparison.get('comparison_date'):
            add('C_comparison', 'comparison', '与上一可比周期的任务变化', ['F005'], boundary='只比较任务量，不推断效率或能耗改善。')
        projects = context['project_distribution']
        if projects:
            top = projects[0]
            related = [r for r in records if (r.project_code or r.ship_no or '未标注工程') == top['project_name']]
            if top['project_name'] == '未标注工程':
                quality.append('部分任务工程信息未填写；缺失工程信息不能解释为工程集中。')
                context['facts'].append(_fact('F010', '工程信息缺口', f"{top['task_count']} 项任务工程号未填写", quality[-1]))
                add('C_project_missing', 'data_quality', '工程信息缺口', ['F010'], [task(r) for r in related], boundary=quality[-1], priority=2)
            else:
                context['facts'].append(_fact('F010', '任务最多工程', f"{top['project_name']} · {top['task_count']} 项", '按本期完整任务集合分组；工程号为空时保留船名。'))
                add('C_project', 'distribution', '工程任务分布', ['F010'], [task(r) for r in related])
        outlier_ids = {r['task_id'] for r in context['anomalies']}
        if outlier_ids:
            add('C_duration', 'duration', '切割时间较长的任务', ['F006' if kind == 'workshop' else 'F005'],
                [task(r) for r in records if r.task_id in outlier_ids],
                boundary='按本期切割时长四分位距筛选；时长较长不代表加工异常或人员效率低。')
        if kind == 'workshop':
            pending = [r for r in records if not r.confirmed_by]
            if pending:
                add('C_confirmation', 'confirmation', '已有切割记录、待工人确认', ['F004', 'F005'], [task(r) for r in pending],
                    boundary='未确认只表示人员与工时归属尚未登记，不表示切割未完成。', priority=3)
            quality.append('工人工时汇总仅包含已确认任务；未确认任务仍计入生产记录。')
        else:
            quality.extend(filter(None, [context['energy_notice'],
                '能耗缺失不等于零；有效合计只覆盖有记录任务，切割时长不等于开机时长。',
                '读数插值或估算不等于模拟；覆盖率或计算口径不同不能推断能源效率变化。']))
            coverage = context['energy_coverage']
            for channel, title in [('gas', '气耗'), ('electricity', '电耗')]:
                valid = coverage[channel]
                rate = f"{valid / count * 100:.1f}%" if count else '暂无任务'
                context['facts'].append(_fact('F011' if channel == 'gas' else 'F012', f'{title}记录覆盖率', rate,
                    f'有效记录 {valid} 项 / 全部 {count} 项；估算或插值 {context["energy_estimates"][channel]} 项。'))
            issues = {'overlap', 'invalid_interval', 'missing', 'gap', 'counter_reset', 'invalid_reading', 'unavailable', 'unconfigured', 'unmapped'}
            affected = [r for r in records if r.gas_consumption is None or r.electricity_consumption is None
                        or {r.gas_data_status, r.electricity_data_status} & issues]
            if affected:
                add('C_energy_quality', 'data_quality', '能耗覆盖及待核对记录', ['F003', 'F004', 'F006', 'F011', 'F012'],
                    [task(r) for r in affected], boundary='先核对仪表关联、采样和任务起止时间，不推断设备故障。', priority=3)
            estimated = [r for r in records if {r.gas_data_status, r.electricity_data_status} & {'interpolated', 'estimated'}]
            if estimated:
                add('C_energy_estimates', 'estimation', '含按仪表读数估算的能耗', ['F003', 'F004', 'F011', 'F012'],
                    [task(r) for r in estimated], boundary='说明计算方式，不称为模拟数据。')
            if records:
                add('C_equipment', 'distribution', '设备任务及切割时长分布', ['F001', 'F002'],
                    [ref('equipment', r.equipment_id) for r in records if r.equipment_id])
    if not count:
        quality.append('当前周期无有效任务记录，无法据此评价生产表现。')
    return context


def model_context(context: dict) -> dict:
    """Full counts and aggregates survive sampling; links never come from the model."""
    candidates = context.get('candidates', [])
    tables = []
    for table in context.get('tables', []):
        rows = table['rows']
        if 'task_id' in (rows[0] if rows else {}):
            ids = {ref['id'] for c in candidates for ref in context['_references'][c['id']][:3] if ref['kind'] == 'task'}
            selected = [r for r in rows if str(r['task_id']) in ids]
        else:
            selected = rows[:40]
        tables.append(dict(name=table['name'], row_count=len(rows), sample_count=len(selected), rows=selected,
            coverage='完整统计覆盖全部记录；仅发送支持候选的任务样本或前40项汇总，不能把样本当作全部。'))
    return dict(scope=context['scope'], facts=context['facts'], candidates=candidates, tables=tables,
        data_quality=context['data_quality'],
        distributions={key: context[key] for key in ('comparisons', 'project_distribution', 'equipment_distribution', 'energy_by_project', 'energy_quality', 'energy_estimates', 'daily_distribution', 'distribution_coverage') if key in context})
