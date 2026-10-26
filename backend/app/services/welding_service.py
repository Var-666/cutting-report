from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from collections import OrderedDict
from threading import Lock
from time import monotonic
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.database.welding import WeldingRepository, job_aggregate, job_conditions, job_source, valid_serial, valid_time
from app.models.domain import to_primitive
from app.models.welding import ReportType, WeldingQuery, WELD_TYPES


PENDING_METRICS = {
    'completed_part_count': '工件完成状态及完成事件尚未确认',
    'arc_rate': '工件用时含义及统计范围待确认',
    'weld_rate': '工件累计长度与生产状态口径待确认',
    'additional_wrap_length_m': '包角附加长度与作业长度的包含关系待确认',
}


def number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def mapped_row(row: dict, kind: str) -> dict:
    result = to_primitive(row)
    for source, target in [('weld_lenght', 'weld_length_m'), ('theory_lenght', 'theoretical_length_m'),
                           ('reality_lenght', 'actual_length_m'), ('wrap_angle_lenght', 'additional_wrap_length_m')]:
        if source in row:
            value = number(row[source])
            result[target] = value / 1000 if value is not None and value >= 0 else None
    if 'used_time' in row:
        duration = number(row['used_time'])
        result['working_hours'] = duration / 3600 if duration is not None and duration >= 0 else None
    result['state_label'] = f'待确认（{row["state"]}）' if row.get('state') is not None else '待确认（空值）'
    if kind == 'jobs':
        code = row.get('weld_type')
        result['weld_type_label'] = WELD_TYPES.get(code, f'未知类型（{code}）')
    result['quality_reasons'] = []
    serial = str(row.get('serial_number') or '').strip()
    if not serial or serial.lower() in ('unknown', 'null'):
        result['quality_reasons'].append('流水号无效')
    if 'weld_lenght' in row and (number(row['weld_lenght']) is None or number(row['weld_lenght']) <= 0):
        result['quality_reasons'].append('长度为空或非正')
    duration = number(row.get('used_time'))
    if duration is None or duration < 0 or kind == 'jobs' and duration == 0:
        result['quality_reasons'].append('用时未记录或无效')
    elif duration > 86400:
        result['quality_reasons'].append('用时超过24小时，需核查')
    for key in ('theory_lenght', 'reality_lenght'):
        if number(row.get(key)) is not None and number(row[key]) < 0:
            result['quality_reasons'].append('理论或实际长度为负')
    if any(number(row.get(key)) is not None and not 0 <= number(row[key]) <= 1 for key in ('weld_rate', 'arc_rate')):
        result['quality_reasons'].append('源表比率超出0至1，需核查')
    weld_duration = number(row.get('weld_used_time'))
    if weld_duration is not None and (weld_duration < 0 or duration is not None and weld_duration > duration):
        result['quality_reasons'].append('焊接用时与总用时关系异常')
    for name in ('start_time', 'end_time'):
        value = row.get(name)
        try:
            parsed = datetime.strptime(str(value), '%Y-%m-%d %H:%M:%S')
            if parsed.strftime('%Y-%m-%d %H:%M:%S') != value:
                raise ValueError
        except (ValueError, TypeError):
            result['quality_reasons'].append(f'{"开始" if name == "start_time" else "结束"}时间无效')
    return result


def summary_values(row: dict) -> dict:
    result = to_primitive(row)
    for key in PENDING_METRICS:
        result[key] = None
    result['wrap_length_m'] = float(row.get('flat_wrap_length_m') or 0) + float(row.get('vertical_wrap_length_m') or 0)
    return result


class WeldingService:
    def __init__(self, settings: Settings, repository: WeldingRepository) -> None:
        self.settings = replace(settings, device_id='welding', device_name='小组立焊接线', production_line_id='welding', production_data_source='welding')
        self.repository = repository
        self._analyses = OrderedDict()
        self._analysis_lock = Lock()

    def cached_analysis(self, version: str):
        with self._analysis_lock:
            value = self._analyses.get(version)
            return value[1] if value and monotonic() - value[0] < 300 else None

    def remember_analysis(self, version: str, result) -> None:
        with self._analysis_lock:
            self._analyses[version] = (monotonic(), result)
            self._analyses.move_to_end(version)
            while len(self._analyses) > 16:
                self._analyses.popitem(last=False)

    def report(self, kind: ReportType, query: WeldingQuery, *, export_limit: int | None = None, dashboard: bool = False) -> dict:
        repo = self.repository
        if kind == 'parts':
            query = query.model_copy(update={'part_state': query.part_state if query.part_state is not None else query.state, 'state': None})
        with repo.client.snapshot() as session:
            summary = summary_values(repo.summary(session, query))
            daily = [summary_values(row) for row in repo.daily(session, query)]
            equipment = [summary_values(row) for row in repo.equipment(session, query)]
            if len(equipment) > 10000:
                raise ValueError('设备机器人工位组合超过上限，请缩小筛选范围')
            info = repo.source_info(session)
            where, values = job_conditions(query)
            source, source_values = job_source(query)
            devices = session.fetch(f'SELECT j.device_no, {job_aggregate()} FROM {source} GROUP BY j.device_no ORDER BY j.device_no LIMIT 1001', source_values, limit=1001)
            today_summary = None
            if dashboard:
                today = datetime.now(ZoneInfo(self.settings.weld_db_timezone)).date()
                today_summary = summary_values(repo.summary(session, query.model_copy(update={'start': today, 'end': today})))
            association = session.fetch(
                f'SELECT COUNT(CASE WHEN p.owners IS NULL THEN 1 END) AS unlinked, '
                f'COUNT(CASE WHEN p.owners > 1 THEN 1 END) AS ambiguous '
                f'FROM (SELECT DISTINCT j.serial_number FROM weld_info_table j WHERE {where} '
                f'AND {valid_serial("j.serial_number")} AND {valid_time("j.end_time")}) involved '
                f'LEFT JOIN (SELECT serial_number, COUNT(*) AS owners FROM weld_part_info_table '
                f'GROUP BY serial_number) p ON p.serial_number = involved.serial_number', values, limit=1)[0]
            projects = []
            if kind == 'parts':
                projects = session.fetch(f'SELECT p.project_no, MIN(p.project_name) AS project_name, COUNT(*) AS part_count, SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END) / 1000 AS cumulative_actual_length_m FROM weld_part_info_table p WHERE (SELECT COUNT(*) FROM weld_part_info_table owner WHERE owner.serial_number = p.serial_number) = 1 AND EXISTS (SELECT 1 FROM weld_info_table j WHERE {where} AND {valid_time("j.end_time")} AND j.serial_number = p.serial_number AND {valid_serial("p.serial_number")}) GROUP BY p.project_no ORDER BY part_count DESC, p.project_no LIMIT 101', values, limit=101)
            speeds = session.fetch(f"SELECT CASE WHEN j.weld_speed IS NULL OR j.weld_speed <= 0 THEN '未记录或无效' WHEN j.weld_speed < 300 THEN '<300' WHEN j.weld_speed < 600 THEN '300–599' ELSE '≥600' END AS speed_band, COUNT(*) AS job_count FROM weld_info_table j WHERE {where} AND {valid_time('j.end_time')} GROUP BY speed_band ORDER BY speed_band", values, limit=5) if kind == 'jobs' else []
            if kind == 'jobs':
                raw, total = repo.jobs(session, query, limit=export_limit)
            elif kind == 'parts':
                raw, total = repo.parts(session, query, limit=export_limit)
            else:
                raw = daily if kind == 'daily' else equipment
                column = {'time': 'production_date' if kind == 'daily' else 'device_no',
                          'length': 'weld_length_m', 'id': 'production_date' if kind == 'daily' else 'device_no'}[query.sort_by]
                raw = sorted(raw, key=lambda row: (row.get(column) is not None, row.get(column) or 0, row.get('robot_no') or 0), reverse=query.descending)
                total = len(raw)
                raw = raw[:export_limit] if export_limit is not None else raw[(query.page - 1) * query.page_size:query.page * query.page_size]
            if kind == 'parts':
                part_where, part_values = job_conditions(query.model_copy(update={'state': None}))
                part_conditions = f'EXISTS (SELECT 1 FROM weld_info_table j WHERE {part_where} AND j.serial_number = p.serial_number AND {valid_serial("p.serial_number")})'
                for name in ('project_no', 'section_no', 'part_no', 'state'):
                    value = query.part_state if name == 'state' else getattr(query, name)
                    if value is not None and value != '':
                        part_conditions += f' AND p.{name} = %s'
                        part_values += (value,)
                totals = session.fetch(f'SELECT COALESCE(SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END), 0) / 1000 AS part_actual_length_m, COALESCE(SUM((SELECT COUNT(*) FROM weld_part_info_table owner WHERE owner.serial_number = p.serial_number) > 1), 0) AS ambiguous_part_rows, COALESCE(SUM(CASE WHEN p.used_time < 0 OR p.weld_used_time < 0 OR p.weld_used_time > p.used_time OR p.weld_rate < 0 OR p.weld_rate > 1 OR p.arc_rate < 0 OR p.arc_rate > 1 THEN 1 ELSE 0 END), 0) AS invalid_part_rows FROM weld_part_info_table p WHERE {part_conditions}', part_values, limit=1)[0]
                if totals['ambiguous_part_rows']:
                    totals['part_actual_length_m'] = None
                summary.update(totals)
                summary['part_row_count'] = total
            rows = [mapped_row(row, kind) for row in raw] if kind in ('parts', 'jobs') else raw
        meta = {
            'line_key': 'welding', 'timezone': self.settings.weld_db_timezone,
            'calculated_at': datetime.now(ZoneInfo(self.settings.weld_db_timezone)).isoformat(),
            'data_updated_at': to_primitive(info['updated_at']), 'devices': info['devices'],
            'units': {'length': 'm', 'working_hours': 'h', 'speed': 'mm/min', 'output_per_hour': 'm/h'},
            'pending_metrics': PENDING_METRICS,
            'quality': {key: summary[key] for key in ('invalid_time_rows', 'invalid_serial_rows', 'invalid_length_rows', 'invalid_duration_rows', 'unverified_state_rows')},
            'unlinked_part_count': association['unlinked'], 'ambiguous_part_count': association['ambiguous'],
            'notices': ['按作业结束时间计入生产日；作业统计包含所有已记录状态，状态业务含义待确认。',
                        '工件字段为累计值，不能当作所选区间新增产出；机器人编号按作业表统计，跨表映射待核实。',
                        '各异常原因可重叠；无法归入时间区间的记录仅在源库核验报告统计。',
                        '速度分布使用源表记录，不代表整线实测平均速度。'],
        }
        if kind == 'parts' and summary.get('ambiguous_part_rows'):
            meta['pending_metrics'] = {**PENDING_METRICS, 'part_actual_length_m': '工件流水号存在多条归属，累计总长度暂待核实'}
        if kind == 'parts':
            meta['quality']['invalid_part_rows'] = summary['invalid_part_rows']
            meta['notices'].append(f'当前涉及工件中有{summary["invalid_part_rows"]}条比率或耗时关系异常候选；原始明细保留，不据此判定质量。')
        meta['notices'].append('项目图仅包含流水号归属唯一且有有效区间作业的工件；歧义关联不自动合并。')
        if association['ambiguous']:
            meta['notices'].append(f'当前有{association["ambiguous"]}个参与流水号存在多条工件归属，相关累计指标及下钻需核实。')
        payload = to_primitive({'report_type': kind, 'query': query.model_dump(mode='json'), 'summary': summary,
                                'daily': daily, 'equipment': equipment, 'device_distribution': devices,
                                'today': today_summary, 'projects': projects[:100],
                                'projects_truncated': len(projects) > 100, 'speed_distribution': speeds,
                                'items': rows, 'total': total, 'meta': meta})
        payload['data_version'] = self.version(payload)
        return payload

    @staticmethod
    def version(payload: dict) -> str:
        # Table pagination and generation time must not change the analysis source version.
        relevant = {key: payload[key] for key in ('summary', 'daily', 'equipment')}
        relevant['quality'] = payload['meta']['quality']
        relevant['source_updates'] = payload['meta']['data_updated_at']
        relevant['association'] = {key: payload['meta'].get(key) for key in ('unlinked_part_count', 'ambiguous_part_count', 'pending_metrics')}
        relevant['filters'] = {key: value for key, value in payload['query'].items() if key not in ('page', 'page_size', 'sort_by', 'descending')}
        return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def dashboard(self, query: WeldingQuery) -> dict:
        return self.report('jobs', query.model_copy(update={'page': 1, 'page_size': 10}), dashboard=True)

    def part(self, part_id: int, page: int = 1, page_size: int = 20) -> dict | None:
        repo = self.repository
        with repo.client.snapshot() as session:
            part = repo.part(session, part_id)
            if part is None:
                return None
            rows, total, ambiguous = repo.seams(session, part, page, page_size)
        return {'part': mapped_row(part, 'parts'), 'items': [mapped_row(row, 'seams') for row in rows],
                'total': total, 'association_status': 'pending_verification' if ambiguous else 'exact_serial_match',
                'notice': '工件流水号无效或存在多条归属，暂不展示焊缝关联' if ambiguous else '按完整流水号精确关联；机器人跨表映射待确认'}

    def health(self) -> dict:
        with self.repository.client.snapshot() as session:
            info = self.repository.source_info(session)
        return to_primitive({'connected': True, **info})
