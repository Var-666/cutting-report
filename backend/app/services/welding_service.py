from __future__ import annotations

import hashlib
import json
import math
from contextlib import nullcontext
from dataclasses import replace
from copy import deepcopy
from collections import OrderedDict
from threading import Lock
from time import monotonic, time
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.services.production_lines import SERVICE_LINES
from app.database.report_snapshots import ReportKey, fingerprint
from app.services.report_snapshots import ReportData, ReportSnapshotService
from app.database.welding import WeldingRepository, WeldingSession, WeldingUnavailable, job_aggregate, job_conditions, job_source, valid_serial, valid_time
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
    def __init__(self, settings: Settings, repository: WeldingRepository, *, line_id: str | None = None, snapshots=None, coordination=None) -> None:
        line = next(item for item in SERVICE_LINES if item['type'] == 'welding' and (line_id is None or item['id'] == line_id))
        self.settings = replace(settings, device_id=line['id'], device_name=line['name'], production_line_id=line['id'], production_data_source=line['type'])
        self.repository = repository
        self._analyses = OrderedDict()
        self._analysis_lock = Lock()
        self._statistics = OrderedDict()
        self._statistics_lock = Lock()
        self.snapshot_service = ReportSnapshotService(self.settings, snapshots, coordination, unavailable_errors=(WeldingUnavailable,)) if snapshots else None
        self.report_provider = self

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

    def report(self, kind: ReportType, query: WeldingQuery, *, export_limit: int | None = None, dashboard: bool = False, force: bool = False, live: bool = False) -> dict:
        repo = self.repository
        if kind == 'parts':
            query = query.model_copy(update={'part_state': query.part_state if query.part_state is not None else query.state, 'state': None})
        statistics = self.fetch_summary(kind, query, force=force) if self.snapshot_service and export_limit is None and not live else None
        today_summary = None
        if statistics is not None and dashboard:
            today = datetime.now(ZoneInfo(self.settings.weld_db_timezone)).date()
            today_query = query.model_copy(update={'start': today, 'end': today})
            today_data = statistics if query.start == today == query.end else self.fetch_summary('daily', today_query, force=force)
            today_summary = today_data.payload['common'][0]
            statistics.meta['today_data_as_of'] = today_data.meta['data_as_of']
            statistics.meta['stale'] |= today_data.meta['stale']
            statistics.meta['refreshing'] |= today_data.meta['refreshing']
        scope = repo.client.snapshot() if statistics is None or kind in ('jobs', 'parts') else nullcontext(None)
        with scope as session:
            if statistics is not None:
                common = statistics.payload['common']
                info = statistics.payload['info']
                projects, speeds, totals = statistics.payload['reports'][kind]
            else:
                info = repo.source_info(session)
                filters = query.model_dump(mode='json', exclude={'page', 'page_size', 'sort_by', 'descending'})
                key = json.dumps({'filters': filters, 'source': to_primitive(info)}, sort_keys=True)
                use_cache = not force and not live and export_limit is None and any(value is not None for value in info['updated_at'].values())
                common = self._cached_statistics(('common', key), lambda: self._common_statistics(session, query), use_cache=use_cache)
                projects, speeds, totals = self._cached_statistics((kind, key), lambda: self._report_statistics(session, kind, query), use_cache=use_cache)
            summary, daily, equipment, devices, association = common
            summary.update(totals)
            if dashboard and statistics is None:
                today = datetime.now(ZoneInfo(self.settings.weld_db_timezone)).date()
                today_summary = summary_values(repo.summary(session, query.model_copy(update={'start': today, 'end': today})))
            if kind == 'jobs':
                raw, total = repo.jobs(session, query, limit=export_limit, total=None if statistics is not None else summary['source_rows'])
            elif kind == 'parts':
                raw, total = repo.parts(session, query, limit=export_limit, total=None if statistics is not None else summary['part_row_count'])
            else:
                raw = daily if kind == 'daily' else equipment
                column = {'time': 'production_date' if kind == 'daily' else 'device_no',
                          'length': 'weld_length_m', 'id': 'production_date' if kind == 'daily' else 'device_no'}[query.sort_by]
                raw = sorted(raw, key=lambda row: (row.get(column) is not None, row.get(column) or 0, row.get('robot_no') or 0), reverse=query.descending)
                total = len(raw)
                raw = raw[:export_limit] if export_limit is not None else raw[(query.page - 1) * query.page_size:query.page * query.page_size]
            rows = [mapped_row(row, kind) for row in raw] if kind in ('parts', 'jobs') else raw
        meta = {
            'line_key': self.settings.device_id, 'timezone': self.settings.weld_db_timezone,
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
        if statistics is not None:
            payload['meta']['calculated_at'] = statistics.meta['refreshed_at']
            payload['meta']['report_data'] = {**statistics.meta, 'report_type': kind, 'data_version': payload['data_version'],
                'details_source': 'database' if kind in ('jobs', 'parts') else statistics.meta['source']}
        return payload

    report_types = ('daily', 'jobs', 'parts', 'equipment', 'dashboard')

    def source_identity(self) -> str:
        return fingerprint({name: getattr(self.settings, name) for name in
            ('device_id', 'weld_enabled', 'weld_db_host', 'weld_db_port', 'weld_db_name',
             'weld_db_user', 'weld_db_password', 'weld_db_timezone')})

    @staticmethod
    def snapshot_eligible(query: WeldingQuery) -> bool:
        excluded = {'page', 'page_size', 'sort_by', 'descending'}
        return query.model_dump(exclude=excluded) == WeldingQuery(start=query.start, end=query.end).model_dump(exclude=excluded)

    def _snapshot_key(self, query: WeldingQuery, report_type: str = 'statistics') -> ReportKey:
        if report_type == 'dashboard':
            query = query.model_copy(update={'page': 1, 'page_size': 10, 'sort_by': 'time', 'descending': True})
        page = report_type == 'dashboard' or report_type.startswith('page:')
        excluded = {'start', 'end'} if page else {'start', 'end', 'page', 'page_size', 'sort_by', 'descending'}
        filters = query.model_dump(mode='json', exclude=excluded)
        return ReportKey(self.settings.device_id, 'statistics' if not page and self.snapshot_eligible(query) else report_type, query.start.isoformat(), query.end.isoformat(), filters, self.source_identity())

    def _snapshot_ttl(self, query: WeldingQuery) -> int:
        if not self.snapshot_eligible(query):
            return max(1, min(30, self.settings.report_data_cache_seconds))
        today = datetime.now(ZoneInfo(self.settings.weld_db_timezone)).date()
        return max(30, self.settings.report_background_refresh_seconds) if query.end >= today.replace(day=1) else max(30, self.settings.leadership_report_history_cache_seconds)

    def _load_statistics_snapshot(self, query: WeldingQuery, report_type: str) -> dict:
        with self.repository.client.snapshot() as session:
            info = self.repository.source_info(session)
            common = self._common_statistics(session, query)
            kinds = ('daily', 'jobs', 'parts', 'equipment') if self.snapshot_eligible(query) else (report_type,)
            reports = {kind: self._report_statistics(session, kind, query) for kind in kinds}
        return to_primitive({'common': common, 'info': info, 'reports': reports})

    def fetch_summary(self, report_type: str, query, *, force: bool = False, requested_at: float | None = None) -> ReportData:
        page_kind = report_type.removeprefix('page:') if report_type.startswith('page:') else None
        if report_type not in (*self.report_types, 'statistics') and page_kind not in ('daily', 'jobs', 'parts', 'equipment'):
            raise ValueError('焊接报表类型不支持')
        if not isinstance(query, WeldingQuery):
            query = WeldingQuery.model_validate(query)
        if report_type == 'dashboard':
            query = query.model_copy(update={'page': 1, 'page_size': 10, 'sort_by': 'time', 'descending': True})
        if self.snapshot_service is None:
            raise ValueError('当前查询不支持统计快照')
        if report_type == 'dashboard':
            load = lambda: self.report('jobs', query, dashboard=True, live=True)
        elif page_kind:
            load = lambda: self.report(page_kind, query, live=True)
        else:
            load = lambda: self._load_statistics_snapshot(query, report_type)
        return self.snapshot_service.read(self._snapshot_key(query, report_type), load,
                                          ttl=self._snapshot_ttl(query), force=force, requested_at=requested_at,
                                          background=requested_at is not None, background_only=report_type == 'dashboard' or page_kind is not None)

    def fetch_detail(self, report_type: str, query) -> dict:
        if report_type not in self.report_types:
            raise ValueError('焊接报表类型不支持')
        if not isinstance(query, WeldingQuery):
            query = WeldingQuery.model_validate(query)
        return self.dashboard(query) if report_type == 'dashboard' else self.page(report_type, query)

    def refresh_summary(self, report_type: str, query: dict, *, requested_at: float | None = None) -> None:
        if (report_type == 'dashboard' or report_type.startswith('page:')) and requested_at is None:
            requested_at = time()
        self.fetch_summary(report_type, query, force=True, requested_at=requested_at)

    def refresh_plan(self) -> list[tuple[dict, int]]:
        if not self.settings.weld_enabled or self.snapshot_service is None:
            return []
        today = datetime.now(ZoneInfo(self.settings.weld_db_timezone)).date()
        homepage = WeldingQuery(start=today - timedelta(days=6), end=today, page_size=10)
        queries = [(homepage, 'dashboard')] + [(WeldingQuery(start=today, end=today), f'page:{kind}') for kind in ('daily', 'jobs', 'parts', 'equipment')]
        return [(self.snapshot_service.task_payload(self._snapshot_key(query, kind)),
                 max(30, self.settings.report_background_refresh_seconds)) for query, kind in queries]

    def _cached_statistics(self, key: tuple[str, str], load: Callable[[], Any], *, use_cache: bool = True) -> Any:
        if not use_cache:
            return load()
        # shortcut: source MAX(update_date) is a watermark, not CDC; expire after 30s until a reliable source revision exists.
        with self._statistics_lock:
            cached = self._statistics.get(key)
            if cached and monotonic() - cached[0] < 30:
                self._statistics.move_to_end(key)
                return deepcopy(cached[1])
        value = load()
        with self._statistics_lock:
            self._statistics[key] = (monotonic(), deepcopy(value))
            self._statistics.move_to_end(key)
            while len(self._statistics) > 32:
                self._statistics.popitem(last=False)
        return value

    def _common_statistics(self, session: WeldingSession, query: WeldingQuery) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
        repo = self.repository
        summary = summary_values(repo.summary(session, query))
        daily = [summary_values(row) for row in repo.daily(session, query)]
        equipment = [summary_values(row) for row in repo.equipment(session, query)]
        if len(equipment) > 10000:
            raise ValueError('设备机器人工位组合超过上限，请缩小筛选范围')
        where, values = job_conditions(query)
        source, source_values = job_source(query)
        devices = session.fetch(f'SELECT j.device_no, {job_aggregate()} FROM {source} GROUP BY j.device_no ORDER BY j.device_no LIMIT 1001', source_values, limit=1001)
        association = session.fetch(
            f'SELECT COUNT(CASE WHEN p.owners IS NULL THEN 1 END) AS unlinked, '
            f'COUNT(CASE WHEN p.owners > 1 THEN 1 END) AS ambiguous '
            f'FROM (SELECT DISTINCT j.serial_number FROM weld_info_table j WHERE {where} '
            f'AND {valid_serial("j.serial_number")} AND {valid_time("j.end_time")}) involved '
            f'LEFT JOIN (SELECT serial_number, COUNT(*) AS owners FROM weld_part_info_table '
            f'GROUP BY serial_number) p ON p.serial_number = involved.serial_number', values, limit=1)[0]
        return summary, daily, equipment, devices, association

    def _report_statistics(self, session: WeldingSession, kind: ReportType, query: WeldingQuery) -> tuple[list[dict], list[dict], dict]:
        repo = self.repository
        where, values = job_conditions(query)
        projects = []
        if kind == 'parts':
            # Group ownership once; repeated jobs must not multiply part counts or cumulative lengths.
            owners = '(SELECT serial_number, COUNT(*) AS owner_count FROM weld_part_info_table GROUP BY serial_number) owners'
            projects = session.fetch(
                f'SELECT p.project_no, MIN(p.project_name) AS project_name, COUNT(*) AS part_count, '
                f'SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END) / 1000 AS cumulative_actual_length_m '
                f'FROM (SELECT DISTINCT j.serial_number FROM weld_info_table j WHERE {where} AND {valid_time("j.end_time")}) involved '
                f'JOIN {owners} ON owners.serial_number = involved.serial_number AND owners.owner_count = 1 '
                f'JOIN weld_part_info_table p ON p.serial_number = owners.serial_number '
                f'WHERE {valid_serial("p.serial_number")} GROUP BY p.project_no ORDER BY part_count DESC, p.project_no LIMIT 101', values, limit=101)
        speeds = session.fetch(f"SELECT CASE WHEN j.weld_speed IS NULL OR j.weld_speed <= 0 THEN '未记录或无效' WHEN j.weld_speed < 300 THEN '<300' WHEN j.weld_speed < 600 THEN '300–599' ELSE '≥600' END AS speed_band, COUNT(*) AS job_count FROM weld_info_table j WHERE {where} AND {valid_time('j.end_time')} GROUP BY speed_band ORDER BY speed_band", values, limit=5) if kind == 'jobs' else []
        totals = {}
        if kind == 'parts':
            part_conditions, part_values = repo.part_conditions(query)
            totals = session.fetch(f'SELECT COUNT(*) AS part_row_count, COALESCE(SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END), 0) / 1000 AS part_actual_length_m, COALESCE(SUM(owners.owner_count > 1), 0) AS ambiguous_part_rows, COALESCE(SUM(CASE WHEN p.used_time < 0 OR p.weld_used_time < 0 OR p.weld_used_time > p.used_time OR p.weld_rate < 0 OR p.weld_rate > 1 OR p.arc_rate < 0 OR p.arc_rate > 1 THEN 1 ELSE 0 END), 0) AS invalid_part_rows FROM weld_part_info_table p JOIN {owners} ON owners.serial_number = p.serial_number WHERE {part_conditions}', part_values, limit=1)[0]
            if totals['ambiguous_part_rows']:
                totals['part_actual_length_m'] = None
        return projects, speeds, totals

    @staticmethod
    def version(payload: dict) -> str:
        # Table pagination and generation time must not change the analysis source version.
        relevant = {key: payload[key] for key in ('summary', 'daily', 'equipment')}
        relevant['quality'] = payload['meta']['quality']
        relevant['source_updates'] = payload['meta']['data_updated_at']
        relevant['association'] = {key: payload['meta'].get(key) for key in ('unlinked_part_count', 'ambiguous_part_count', 'pending_metrics')}
        relevant['filters'] = {key: value for key, value in payload['query'].items() if key not in ('page', 'page_size', 'sort_by', 'descending')}
        return hashlib.sha256(json.dumps(relevant, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def dashboard(self, query: WeldingQuery, *, force: bool = False) -> dict:
        query = query.model_copy(update={'page': 1, 'page_size': 10, 'sort_by': 'time', 'descending': True})
        if self.snapshot_service is None:
            return self.report('jobs', query, dashboard=True, force=force)
        return self.page('dashboard', query, force=force)

    def page(self, kind: str, query: WeldingQuery, *, force: bool = False) -> dict:
        if kind not in self.report_types:
            raise ValueError('焊接报表类型不支持')
        if kind == 'parts':
            query = query.model_copy(update={'part_state': query.part_state if query.part_state is not None else query.state, 'state': None})
        if self.snapshot_service is None:
            return self.dashboard(query, force=force) if kind == 'dashboard' else self.report(kind, query, force=force)
        data = self.fetch_summary('dashboard' if kind == 'dashboard' else f'page:{kind}', query, force=force)
        payload = data.payload
        payload['meta']['report_data'] = {**data.meta, 'data_version': payload['data_version'], 'details_source': data.meta['source']}
        return payload

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
