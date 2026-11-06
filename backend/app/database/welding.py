"""Read-only welding queries; business SQL stays separate from MySQL connections."""
from __future__ import annotations

from typing import Any

from app.models.welding import WeldingQuery
from app.database.mysql import MySqlClient, MySqlSession, MySqlUnavailable as WeldingUnavailable

TABLES = ('weld_info_table', 'weld_part_info_table', 'welding_seam_info_table')


def aggregate_sort_key(row: dict, keys: tuple[str, ...]) -> tuple:
    # MySQL ASC puts NULL before values, including the valid identifier 0.
    return tuple((row.get(key) is not None, row.get(key) or 0) for key in keys)


def valid_serial(column: str) -> str:
    return f"({column} IS NOT NULL AND TRIM({column}) <> '' AND LOWER(TRIM({column})) NOT IN ('unknown', 'null'))"


def valid_time(column: str) -> str:
    pattern = r'^[1-9][0-9]{3}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01]) ([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$'
    shape = f"(CHAR_LENGTH({column}) = 19 AND {column} REGEXP '{pattern}')"
    # Only a validated year/month reaches DATE conversion, including on strict MySQL servers.
    month = f"CASE WHEN {shape} THEN CONCAT(LEFT({column}, 7), '-01') ELSE '2000-01-01' END"
    return f"({shape} AND CAST(SUBSTRING({column}, 9, 2) AS UNSIGNED) <= DAY(LAST_DAY({month})))"


JOB_FIELDS = 'id, serial_number, device_no, robot_no, riser_no, welding_seam_no, weld_task_serial_number, weld_type, weld_speed, weld_lenght, wrap_angle_lenght, used_time, start_time, end_time, state, update_date'
PART_FIELDS = 'id, serial_number, project_no, project_name, section_no, part_no, device_no, theory_lenght, reality_lenght, weld_rate, arc_rate, used_time, weld_used_time, start_time, end_time, state, update_date'
SEAM_FIELDS = 'id, serial_number, device_no, robot_no, riser_no, welding_seam_no, theory_lenght, reality_lenght, used_time, start_time, end_time, state, update_date'


def device_scope(column: str, device_nos: tuple[int, ...]) -> str:
    return f" AND {column} IN ({','.join('%s' for _ in device_nos)})" if device_nos else ""


def job_conditions(query: WeldingQuery, alias: str = 'j', device_nos: tuple[int, ...] = ()) -> tuple[str, tuple]:
    start, end = query.bounds
    clauses = [f'{alias}.end_time >= %s', f'{alias}.end_time < %s']
    values: list[Any] = [start, end]
    if device_nos:
        clauses.append(f"{alias}.device_no IN ({','.join('%s' for _ in device_nos)})")
        values.extend(device_nos)
    for name in ('device_no', 'robot_no', 'weld_type', 'serial_number'):
        value = getattr(query, name)
        if value is not None and value != '':
            clauses.append(f'{alias}.{name} = %s')
            values.append(value)
    if query.state is not None:
        clauses.append(f'{alias}.state = %s')
        values.append(query.state)
    if any((query.project_no, query.section_no, query.part_no)) or query.part_state is not None:
        linked = [f'p.serial_number = {alias}.serial_number', valid_serial('p.serial_number') + device_scope('p.device_no', device_nos)]
        values.extend(device_nos)
        for name in ('project_no', 'section_no', 'part_no'):
            value = getattr(query, name)
            if value:
                linked.append(f'p.{name} = %s')
                values.append(value)
        if query.part_state is not None:
            linked.append('p.state = %s')
            values.append(query.part_state)
        clauses.append(f"EXISTS (SELECT 1 FROM weld_part_info_table p WHERE {' AND '.join(linked)})")
    if query.anomalies_only:
        clauses.append(f'({alias}.weld_lenght IS NULL OR {alias}.weld_lenght <= 0 OR {alias}.used_time IS NULL OR {alias}.used_time <= 0 OR NOT COALESCE({valid_serial(f"{alias}.serial_number")}, 0) OR NOT COALESCE({valid_time(f"{alias}.end_time")}, 0))')
    return ' AND '.join(clauses), tuple(values)


def job_source(query: WeldingQuery, device_nos: tuple[int, ...] = ()) -> tuple[str, tuple]:
    sql, values = job_rows(query, device_nos=device_nos)
    return f'({sql}) j', values


def job_rows(query: WeldingQuery, alias: str = 'j', device_nos: tuple[int, ...] = ()) -> tuple[str, tuple]:
    where, values = job_conditions(query, alias, device_nos)
    # LIMIT prevents derived-table merging so validation runs once per source row.
    return (f'SELECT serial_number, device_no, robot_no, end_time, weld_type, weld_lenght, used_time, '
            f'{valid_time(f"{alias}.end_time")} AS valid_end_time, {valid_serial(f"{alias}.serial_number")} AS valid_serial_number '
            f'FROM weld_info_table {alias} WHERE {where} LIMIT 18446744073709551615', values)


def job_aggregates(alias: str = 'j') -> dict[str, str]:
    time = f'{alias}.valid_end_time'
    length = f'({time} AND {alias}.weld_lenght > 0)'
    duration = f'({time} AND {alias}.used_time > 0)'
    paired = f'({length} AND {alias}.used_time > 0)'
    serial = f'{alias}.valid_serial_number'
    columns = {
        'source_rows': 'COUNT(*)', 'job_count': f'COALESCE(SUM({time}), 0)',
        'involved_part_count': f'COUNT(DISTINCT CASE WHEN {time} AND {serial} THEN {alias}.serial_number END)',
        'weld_length_m': f'COALESCE(SUM(CASE WHEN {length} THEN {alias}.weld_lenght ELSE 0 END), 0) / 1000',
        'working_hours': f'COALESCE(SUM(CASE WHEN {duration} THEN {alias}.used_time ELSE 0 END), 0) / 3600',
        'output_per_hour': f'SUM(CASE WHEN {paired} THEN {alias}.weld_lenght ELSE 0 END) / 1000 / NULLIF(SUM(CASE WHEN {paired} THEN {alias}.used_time ELSE 0 END) / 3600, 0)',
        'device_count': f'COUNT(DISTINCT CASE WHEN {time} THEN {alias}.device_no END)',
        'robot_station_count': f'COUNT(DISTINCT CASE WHEN {time} THEN {alias}.device_no END, CASE WHEN {time} THEN {alias}.robot_no END)',
        'invalid_time_rows': f'COALESCE(SUM(NOT COALESCE({time}, 0)), 0)',
        'invalid_serial_rows': f'COALESCE(SUM(NOT COALESCE({serial}, 0)), 0)',
        'invalid_length_rows': f'COALESCE(SUM({alias}.weld_lenght IS NULL OR {alias}.weld_lenght <= 0), 0)',
        'invalid_duration_rows': f'COALESCE(SUM({alias}.used_time IS NULL OR {alias}.used_time <= 0), 0)',
        'unverified_state_rows': 'COUNT(*)',
    }
    for code, name in ((0, 'flat_wrap_length_m'), (1, 'flat_length_m'), (2, 'vertical_wrap_length_m'), (3, 'vertical_length_m')):
        columns[name] = f'COALESCE(SUM(CASE WHEN {length} AND {alias}.weld_type = {code} THEN {alias}.weld_lenght ELSE 0 END), 0) / 1000'
    columns['unknown_length_m'] = f'COALESCE(SUM(CASE WHEN {length} AND ({alias}.weld_type IS NULL OR {alias}.weld_type NOT IN (0,1,2,3)) THEN {alias}.weld_lenght ELSE 0 END), 0) / 1000'
    return columns


def job_aggregate(alias: str = 'j') -> str:
    return ', '.join(f'{expression} AS {name}' for name, expression in job_aggregates(alias).items())


class WeldingRepository:
    def __init__(self, client: MySqlClient, *, device_nos: tuple[int, ...] = ()) -> None:
        self.client = client
        self.device_nos = device_nos

    def conditions(self, query: WeldingQuery, alias: str = "j") -> tuple[str, tuple]:
        return job_conditions(query, alias, self.device_nos)

    def summary(self, session: MySqlSession, query: WeldingQuery) -> dict:
        source, values = job_source(query, self.device_nos)
        return session.fetch(f'SELECT {job_aggregate()} FROM {source}', values, limit=1)[0]

    def daily(self, session: MySqlSession, query: WeldingQuery) -> list[dict]:
        source, values = job_source(query, self.device_nos)
        return session.fetch(f'SELECT LEFT(j.end_time, 10) AS production_date, {job_aggregate()} FROM {source} WHERE j.valid_end_time GROUP BY LEFT(j.end_time, 10) ORDER BY production_date', values, limit=367)

    def equipment(self, session: MySqlSession, query: WeldingQuery) -> list[dict]:
        source, values = job_source(query, self.device_nos)
        return session.fetch(f'SELECT j.device_no, j.robot_no, {job_aggregate()} FROM {source} GROUP BY j.device_no, j.robot_no ORDER BY j.device_no, j.robot_no LIMIT 10001', values, limit=10001)

    def speed_distribution(self, session: MySqlSession, query: WeldingQuery) -> list[dict]:
        where, values = self.conditions(query)
        return session.fetch(f"SELECT CASE WHEN j.weld_speed IS NULL OR j.weld_speed <= 0 THEN '未记录或无效' WHEN j.weld_speed < 300 THEN '<300' WHEN j.weld_speed < 600 THEN '300–599' ELSE '≥600' END AS speed_band, COUNT(*) AS job_count FROM weld_info_table j WHERE {where} AND {valid_time('j.end_time')} GROUP BY speed_band ORDER BY speed_band", values, limit=5)

    def statistics(self, session: MySqlSession, query: WeldingQuery) -> tuple[dict, list[dict], list[dict], list[dict], dict]:
        source, values = job_rows(query, 'source_job', self.device_nos)
        aggregates = job_aggregate()
        empty_metrics = ', '.join(f'NULL AS {name}' for name in job_aggregates())
        # A materialized CTE is shared by all five branches; the source interval is scanned once.
        sql = f'''WITH scoped_jobs AS ({source}),
            involved AS (SELECT DISTINCT serial_number FROM scoped_jobs WHERE valid_end_time AND valid_serial_number),
            owners AS (SELECT p.serial_number, COUNT(*) AS owner_count FROM weld_part_info_table p
                JOIN involved i ON i.serial_number = p.serial_number WHERE 1=1{device_scope('p.device_no', self.device_nos)} GROUP BY p.serial_number)
            SELECT 'summary' AS bucket, NULL AS production_date, NULL AS device_no, NULL AS robot_no,
                {aggregates}, NULL AS unlinked, NULL AS ambiguous FROM scoped_jobs j
            UNION ALL
            SELECT 'daily', LEFT(j.end_time, 10), NULL, NULL, {aggregates}, NULL, NULL
                FROM scoped_jobs j WHERE j.valid_end_time GROUP BY LEFT(j.end_time, 10)
            UNION ALL
            (SELECT 'equipment', NULL, j.device_no, j.robot_no, {aggregates}, NULL, NULL
                FROM scoped_jobs j GROUP BY j.device_no, j.robot_no ORDER BY j.device_no, j.robot_no LIMIT 10001)
            UNION ALL
            (SELECT 'devices', NULL, j.device_no, NULL, {aggregates}, NULL, NULL
                FROM scoped_jobs j GROUP BY j.device_no ORDER BY j.device_no LIMIT 1001)
            UNION ALL
            SELECT 'association', NULL, NULL, NULL, {empty_metrics},
                COUNT(CASE WHEN o.owner_count IS NULL THEN 1 END), COUNT(CASE WHEN o.owner_count > 1 THEN 1 END)
                FROM involved i LEFT JOIN owners o ON o.serial_number = i.serial_number'''
        groups = {'summary': [], 'daily': [], 'equipment': [], 'devices': [], 'association': []}
        dimensions = {'summary': (), 'daily': ('production_date',), 'equipment': ('device_no', 'robot_no'), 'devices': ('device_no',)}
        for row in session.fetch(sql, (*values, *self.device_nos), limit=11371):
            bucket = row.pop('bucket')
            if bucket == 'association':
                groups[bucket].append({key: row[key] for key in ('unlinked', 'ambiguous')})
            else:
                groups[bucket].append({key: value for key, value in row.items()
                                      if key in dimensions[bucket] or key not in ('production_date', 'device_no', 'robot_no', 'unlinked', 'ambiguous')})
        groups['daily'].sort(key=lambda row: row['production_date'])
        for bucket, keys in (('equipment', ('device_no', 'robot_no')), ('devices', ('device_no',))):
            groups[bucket].sort(key=lambda row: aggregate_sort_key(row, keys))
        return groups['summary'][0], groups['daily'], groups['equipment'], groups['devices'], groups['association'][0]

    def jobs(self, session: MySqlSession, query: WeldingQuery, *, limit: int | None = None, total: int | None = None) -> tuple[list[dict], int]:
        where, values = self.conditions(query)
        if total is None:
            total = session.fetch(f'SELECT COUNT(*) AS total FROM weld_info_table j WHERE {where}', values, limit=1)[0]['total']
        if limit is not None and total >= limit:
            return [], total
        column = {'time': 'end_time', 'length': 'weld_lenght', 'id': 'id'}[query.sort_by]
        direction = 'DESC' if query.descending else 'ASC'
        size = limit if limit is not None else query.page_size
        offset = 0 if limit is not None else (query.page - 1) * size
        rows = session.fetch(f'SELECT {JOB_FIELDS} FROM weld_info_table j WHERE {where} ORDER BY {column} {direction}, id {direction} LIMIT %s OFFSET %s', (*values, size, offset), limit=size)
        return rows, total

    @staticmethod
    def part_filters(query: WeldingQuery) -> tuple[str, tuple]:
        clauses, values = [], []
        for name in ('project_no', 'section_no', 'part_no', 'state'):
            value = query.part_state if name == 'state' else getattr(query, name)
            if value is not None and value != '':
                clauses.append(f'p.{name} = %s')
                values.append(value)
        return ' AND '.join(clauses), tuple(values)

    def part_conditions(self, query: WeldingQuery) -> tuple[str, tuple]:
        # State on this page belongs to the part, not to the job that establishes involvement.
        where, values = self.conditions(query.model_copy(update={'state': None}))
        related = f'p.serial_number = j.serial_number AND {valid_serial("p.serial_number")}'
        conditions = f"EXISTS (SELECT 1 FROM weld_info_table j WHERE {where} AND {related}){device_scope('p.device_no', self.device_nos)}"
        values += self.device_nos
        own, own_values = WeldingRepository.part_filters(query)
        if own:
            conditions += f' AND {own}'
            values += own_values
        return conditions, values

    def part_scope(self, query: WeldingQuery) -> tuple[str, tuple]:
        where, values = self.conditions(query.model_copy(update={'state': None}), 'part_job')
        fields = ', '.join(f'source_part.{field}' for field in PART_FIELDS.split(', '))
        # Keep every owner of an involved serial until after ownership is counted.
        return (f'''WITH involved AS (
            SELECT part_job.serial_number, MAX({valid_time('part_job.end_time')}) AS has_valid_job
            FROM weld_info_table part_job WHERE {where} GROUP BY part_job.serial_number),
            scoped_parts AS (SELECT {fields}, i.has_valid_job FROM weld_part_info_table source_part
                JOIN involved i ON i.serial_number = source_part.serial_number
                WHERE {valid_serial('source_part.serial_number')}{device_scope('source_part.device_no', self.device_nos)} LIMIT 18446744073709551615)''', (*values, *self.device_nos))

    def part_statistics(self, session: MySqlSession, query: WeldingQuery) -> tuple[list[dict], dict]:
        scope, values = self.part_scope(query)
        own, own_values = self.part_filters(query)
        sql = f'''{scope}, owners AS (SELECT serial_number, COUNT(*) AS owner_count FROM scoped_parts GROUP BY serial_number)
            SELECT 'totals' AS bucket, NULL AS project_no, NULL AS project_name, NULL AS part_count, NULL AS cumulative_actual_length_m,
                COUNT(*) AS part_row_count,
                COALESCE(SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END), 0) / 1000 AS part_actual_length_m,
                COALESCE(SUM(o.owner_count > 1), 0) AS ambiguous_part_rows,
                COALESCE(SUM(CASE WHEN p.used_time < 0 OR p.weld_used_time < 0 OR p.weld_used_time > p.used_time
                    OR p.weld_rate < 0 OR p.weld_rate > 1 OR p.arc_rate < 0 OR p.arc_rate > 1 THEN 1 ELSE 0 END), 0) AS invalid_part_rows
                FROM scoped_parts p JOIN owners o ON o.serial_number = p.serial_number WHERE {own or '1=1'}
            UNION ALL
            SELECT * FROM (SELECT 'project' AS bucket, p.project_no, MIN(p.project_name) AS project_name, COUNT(*) AS part_count,
                SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END) / 1000 AS cumulative_actual_length_m,
                NULL AS part_row_count, NULL AS part_actual_length_m, NULL AS ambiguous_part_rows, NULL AS invalid_part_rows
                FROM scoped_parts p JOIN owners o ON o.serial_number = p.serial_number
                WHERE p.has_valid_job AND o.owner_count = 1
                GROUP BY p.project_no ORDER BY part_count DESC, p.project_no LIMIT 101) projects
            ORDER BY bucket DESC, part_count DESC, project_no'''
        rows = session.fetch(sql, (*values, *own_values), limit=102)
        total_row = next(row for row in rows if row['bucket'] == 'totals')
        totals = {key: total_row[key] for key in ('part_row_count', 'part_actual_length_m', 'ambiguous_part_rows', 'invalid_part_rows')}
        if totals['ambiguous_part_rows']:
            totals['part_actual_length_m'] = None
        projects = [{key: row[key] for key in ('project_no', 'project_name', 'part_count', 'cumulative_actual_length_m')}
                    for row in rows if row['bucket'] == 'project']
        return projects, totals

    def parts(self, session: MySqlSession, query: WeldingQuery, *, limit: int | None = None, total: int | None = None) -> tuple[list[dict], int]:
        scope, values = self.part_scope(query)
        own, own_values = self.part_filters(query)
        values += own_values
        if total is None:
            total = session.fetch(f'{scope}\nSELECT COUNT(*) AS total FROM scoped_parts p WHERE {own or "1=1"}', values, limit=1)[0]['total']
        if limit is not None and total >= limit:
            return [], total
        size = limit if limit is not None else query.page_size
        offset = 0 if limit is not None else (query.page - 1) * size
        column = {'time': 'end_time', 'length': 'reality_lenght', 'id': 'id'}[query.sort_by]
        direction = 'DESC' if query.descending else 'ASC'
        rows = session.fetch(f'{scope}\nSELECT {PART_FIELDS} FROM scoped_parts p WHERE {own or "1=1"} ORDER BY {column} {direction}, id {direction} LIMIT %s OFFSET %s', (*values, size, offset), limit=size)
        return rows, total

    def part(self, session: MySqlSession, part_id: int) -> dict | None:
        scope = device_scope("p.device_no", self.device_nos)
        values = (part_id, *self.device_nos)
        if self.device_nos:
            scope += f" AND EXISTS (SELECT 1 FROM weld_info_table j WHERE j.serial_number = p.serial_number AND j.device_no IN ({','.join('%s' for _ in self.device_nos)}))"
            values += self.device_nos
        rows = session.fetch(f'SELECT {PART_FIELDS} FROM weld_part_info_table p WHERE id = %s{scope}', values, limit=1)
        return rows[0] if rows else None

    def seams(self, session: MySqlSession, part: dict, page: int, page_size: int) -> tuple[list[dict], int, bool]:
        serial = part['serial_number']
        if not serial or str(serial).strip().lower() in ('unknown', 'null'):
            return [], 0, True
        scope = device_scope('device_no', self.device_nos)
        values = (serial, *self.device_nos)
        owners = session.fetch(f'SELECT COUNT(*) AS total FROM weld_part_info_table WHERE serial_number = %s{scope}', values, limit=1)[0]['total']
        if owners != 1:
            return [], 0, True
        total = session.fetch(f'SELECT COUNT(*) AS total FROM welding_seam_info_table WHERE serial_number = %s{scope}', values, limit=1)[0]['total']
        if page_size > 100 and total >= page_size:
            return [], total, False
        rows = session.fetch(f'SELECT {SEAM_FIELDS} FROM welding_seam_info_table WHERE serial_number = %s{scope} ORDER BY riser_no, welding_seam_no, id LIMIT %s OFFSET %s', (*values, page_size, (page - 1) * page_size), limit=page_size)
        return rows, total, False

    def source_info(self, session: MySqlSession) -> dict:
        scope = device_scope("device_no", self.device_nos)
        rows = session.fetch(f'SELECT device_no FROM weld_info_table WHERE device_no >= 0{scope} GROUP BY device_no ORDER BY device_no LIMIT 1001', self.device_nos, limit=1001)
        if len(rows) > 1000:
            raise WeldingUnavailable('源库设备目录超过支持范围，请联系管理员核查')
        updated = {table: session.fetch(f'SELECT MAX(update_date) AS updated_at FROM {table} WHERE 1=1{scope}', self.device_nos, limit=1)[0]['updated_at'] for table in TABLES}
        return {'devices': [row['device_no'] for row in rows], 'updated_at': updated}
