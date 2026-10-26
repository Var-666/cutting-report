"""Read-only welding queries; each report uses one repeatable-read snapshot."""
from __future__ import annotations

import logging
import hashlib
from math import ceil
from contextlib import contextmanager
from threading import Lock
from time import monotonic
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config import Settings
from app.models.welding import WeldingQuery
from app.services.report_deadline import remaining_seconds
from app.services.request_cancellation import SqlRequestCancelled, current_request_id, sql_request_cancellations


logger = logging.getLogger(__name__)
TABLES = ('weld_info_table', 'weld_part_info_table', 'welding_seam_info_table')


class WeldingUnavailable(RuntimeError):
    def __init__(self, message: str, *, category: str = 'source_unavailable') -> None:
        super().__init__(message)
        self.category = category


class WeldingSession:
    def __init__(self, connection, timeout: int = 120) -> None:
        self.connection = connection
        self.timeout = timeout

    def fetch(self, sql: str, values: tuple = (), *, limit: int = 100001) -> list[dict[str, Any]]:
        if sql_request_cancellations.is_cancelled(current_request_id.get()):
            raise SqlRequestCancelled('焊接查询已取消')
        started = monotonic()
        remaining = remaining_seconds()
        if remaining is not None and remaining <= 0:
            raise WeldingUnavailable('焊接查询超时，请缩小日期或筛选范围后重试', category='query_timeout')
        timeout = self.timeout if remaining is None else max(1, min(self.timeout, ceil(remaining)))
        query_id = hashlib.sha256(sql.encode()).hexdigest()[:12]
        cursor = self.connection.cursor(dictionary=True, read_timeout=timeout, write_timeout=timeout)
        failed = False
        try:
            cursor.execute(sql, values)
            rows = cursor.fetchmany(limit)
            if sql_request_cancellations.is_cancelled(current_request_id.get()):
                raise SqlRequestCancelled('焊接查询已取消')
            logger.info('welding query request_id=%s id=%s elapsed_ms=%d rows=%d', current_request_id.get(), query_id, round((monotonic() - started) * 1000), len(rows))
            return rows
        except Exception as exc:
            failed = True
            logger.warning('welding query failed request_id=%s id=%s type=%s errno=%s elapsed_ms=%d', current_request_id.get(), query_id, type(exc).__name__, getattr(exc, 'errno', None), round((monotonic() - started) * 1000))
            raise
        finally:
            try:
                cursor.close()
            except Exception as exc:
                if not failed:
                    raise
                logger.warning('welding cursor cleanup failure type=%s errno=%s', type(exc).__name__, getattr(exc, 'errno', None))


class WeldingClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pool = None
        self._lock = Lock()

    def _get_pool(self):
        cfg = self.settings
        if not cfg.weld_enabled:
            raise WeldingUnavailable('小组立焊接线尚未启用')
        if not all((cfg.weld_db_host, cfg.weld_db_name, cfg.weld_db_user, cfg.weld_db_password)):
            raise WeldingUnavailable('焊接数据库配置不完整，请联系管理员')
        if not (1 <= cfg.weld_db_port <= 65535 and 1 <= cfg.weld_db_pool_size <= 4
                and 1 <= cfg.weld_db_connect_timeout <= 60 and 1 <= cfg.weld_db_query_timeout <= 300):
            raise WeldingUnavailable('焊接数据库端口、连接容量或超时配置无效')
        ZoneInfo(cfg.weld_db_timezone)
        with self._lock:
            if self._pool is None:
                from mysql.connector.pooling import MySQLConnectionPool
                self._pool = MySQLConnectionPool(
                    pool_name=f'weld_{uuid4().hex[:16]}', pool_size=cfg.weld_db_pool_size,
                    host=cfg.weld_db_host, port=cfg.weld_db_port, database=cfg.weld_db_name,
                    user=cfg.weld_db_user, password=cfg.weld_db_password, charset='utf8mb4',
                    connection_timeout=cfg.weld_db_connect_timeout,
                    read_timeout=cfg.weld_db_query_timeout, write_timeout=cfg.weld_db_query_timeout,
                    use_pure=True, autocommit=False,
                )
            return self._pool

    @contextmanager
    def snapshot(self) -> Iterator[WeldingSession]:
        connection = None
        pool = None
        discard_pool = False
        ready = False
        try:
            pool = self._get_pool()
            connection = pool.get_connection()
            connection.start_transaction(isolation_level='REPEATABLE READ', consistent_snapshot=True, readonly=True)
            ready = True
            yield WeldingSession(connection, self.settings.weld_db_query_timeout)
        except (WeldingUnavailable, SqlRequestCancelled):
            raise
        except ValueError:
            if ready:
                raise
            raise WeldingUnavailable('焊接连接配置无效，请检查后端配置') from None
        except Exception as exc:
            # Driver messages can contain credentials, SQL values and host addresses.
            discard_pool = True
            logger.warning('welding source failure request_id=%s type=%s errno=%s', current_request_id.get(), type(exc).__name__, getattr(exc, 'errno', None))
            code = getattr(exc, 'errno', None)
            if type(exc).__name__ == 'PoolError':
                discard_pool = False
                raise WeldingUnavailable('焊接数据库连接池已耗尽，请稍后重试', category='connection_pool_exhausted') from None
            if isinstance(exc, TimeoutError) or type(exc).__name__ in ('ReadTimeoutError', 'WriteTimeoutError') or code in (1205, 1317, 3024):
                raise WeldingUnavailable('焊接数据库查询超时，请缩小筛选范围或联系管理员核查SQL耗时', category='query_timeout') from None
            if code == 1045:
                raise WeldingUnavailable('焊接数据库认证失败，请检查账号和密码', category='authentication_failed') from None
            if code in (1142, 1143, 1227):
                raise WeldingUnavailable('焊接数据库只读查询权限不足，请联系管理员核对授权', category='permission_denied') from None
            if code in (1049, 1054, 1146):
                raise WeldingUnavailable('焊接数据库或表结构与预期不符，请核对数据库和字段映射', category='schema_mismatch') from None
            if code in (1064, 1149):
                raise WeldingUnavailable('焊接SQL执行失败，请联系管理员核查查询语句', category='sql_error') from None
            if code in (2002, 2003, 2005, 2006, 2013, 2055):
                raise WeldingUnavailable('焊接数据库连接失败或连接中断，请核查网络及数据库服务', category='connection_failed') from None
            if isinstance(exc, ImportError):
                raise WeldingUnavailable('服务器尚未安装焊接MySQL驱动，请更新部署依赖') from None
            raise WeldingUnavailable('焊接数据源暂不可用或查询超时，请稍后重试') from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception as exc:
                    discard_pool = True
                    logger.warning('welding rollback failure type=%s errno=%s', type(exc).__name__, getattr(exc, 'errno', None))
                finally:
                    try:
                        connection.close()
                    except Exception as exc:
                        discard_pool = True
                        logger.warning('welding connection cleanup failure type=%s errno=%s', type(exc).__name__, getattr(exc, 'errno', None))
            if discard_pool and pool is not None:
                # A timed-out connector can retain unread protocol data even after reconnect.
                with self._lock:
                    if self._pool is pool:
                        self._pool = None


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


def job_conditions(query: WeldingQuery, alias: str = 'j') -> tuple[str, tuple]:
    start, end = query.bounds
    clauses = [f'{alias}.end_time >= %s', f'{alias}.end_time < %s']
    values: list[Any] = [start, end]
    for name in ('device_no', 'robot_no', 'weld_type', 'serial_number'):
        value = getattr(query, name)
        if value is not None and value != '':
            clauses.append(f'{alias}.{name} = %s')
            values.append(value)
    if query.state is not None:
        clauses.append(f'{alias}.state = %s')
        values.append(query.state)
    if any((query.project_no, query.section_no, query.part_no)) or query.part_state is not None:
        linked = [f'p.serial_number = {alias}.serial_number', valid_serial('p.serial_number')]
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


def job_source(query: WeldingQuery) -> tuple[str, tuple]:
    where, values = job_conditions(query)
    # LIMIT prevents derived-table merging so validation runs once per source row.
    return (f'(SELECT serial_number, device_no, robot_no, end_time, weld_type, weld_lenght, used_time, '
            f'{valid_time("j.end_time")} AS valid_end_time, {valid_serial("j.serial_number")} AS valid_serial_number '
            f'FROM weld_info_table j WHERE {where} LIMIT 18446744073709551615) j', values)


def job_aggregate(alias: str = 'j') -> str:
    time = f'{alias}.valid_end_time'
    length = f'({time} AND {alias}.weld_lenght > 0)'
    duration = f'({time} AND {alias}.used_time > 0)'
    paired = f'({length} AND {alias}.used_time > 0)'
    serial = f'{alias}.valid_serial_number'
    columns = [
        f'COUNT(*) AS source_rows', f'COALESCE(SUM({time}), 0) AS job_count',
        f'COUNT(DISTINCT CASE WHEN {time} AND {serial} THEN {alias}.serial_number END) AS involved_part_count',
        f'COALESCE(SUM(CASE WHEN {length} THEN {alias}.weld_lenght ELSE 0 END), 0) / 1000 AS weld_length_m',
        f'COALESCE(SUM(CASE WHEN {duration} THEN {alias}.used_time ELSE 0 END), 0) / 3600 AS working_hours',
        f'SUM(CASE WHEN {paired} THEN {alias}.weld_lenght ELSE 0 END) / 1000 / NULLIF(SUM(CASE WHEN {paired} THEN {alias}.used_time ELSE 0 END) / 3600, 0) AS output_per_hour',
        f'COUNT(DISTINCT CASE WHEN {time} THEN {alias}.device_no END) AS device_count',
        f'COUNT(DISTINCT CASE WHEN {time} THEN {alias}.device_no END, CASE WHEN {time} THEN {alias}.robot_no END) AS robot_station_count',
        f'COALESCE(SUM(NOT COALESCE({time}, 0)), 0) AS invalid_time_rows',
        f'COALESCE(SUM(NOT COALESCE({serial}, 0)), 0) AS invalid_serial_rows',
        f'COALESCE(SUM({alias}.weld_lenght IS NULL OR {alias}.weld_lenght <= 0), 0) AS invalid_length_rows',
        f'COALESCE(SUM({alias}.used_time IS NULL OR {alias}.used_time <= 0), 0) AS invalid_duration_rows',
        f'COUNT(*) AS unverified_state_rows',
    ]
    for code, name in ((0, 'flat_wrap_length_m'), (1, 'flat_length_m'), (2, 'vertical_wrap_length_m'), (3, 'vertical_length_m')):
        columns.append(f'COALESCE(SUM(CASE WHEN {length} AND {alias}.weld_type = {code} THEN {alias}.weld_lenght ELSE 0 END), 0) / 1000 AS {name}')
    columns.append(f'COALESCE(SUM(CASE WHEN {length} AND ({alias}.weld_type IS NULL OR {alias}.weld_type NOT IN (0,1,2,3)) THEN {alias}.weld_lenght ELSE 0 END), 0) / 1000 AS unknown_length_m')
    return ', '.join(columns)


class WeldingRepository:
    def __init__(self, client: WeldingClient) -> None:
        self.client = client

    def summary(self, session: WeldingSession, query: WeldingQuery) -> dict:
        source, values = job_source(query)
        return session.fetch(f'SELECT {job_aggregate()} FROM {source}', values, limit=1)[0]

    def daily(self, session: WeldingSession, query: WeldingQuery) -> list[dict]:
        source, values = job_source(query)
        return session.fetch(f'SELECT LEFT(j.end_time, 10) AS production_date, {job_aggregate()} FROM {source} WHERE j.valid_end_time GROUP BY LEFT(j.end_time, 10) ORDER BY production_date', values, limit=367)

    def equipment(self, session: WeldingSession, query: WeldingQuery) -> list[dict]:
        source, values = job_source(query)
        return session.fetch(f'SELECT j.device_no, j.robot_no, {job_aggregate()} FROM {source} GROUP BY j.device_no, j.robot_no ORDER BY j.device_no, j.robot_no LIMIT 10001', values, limit=10001)

    def jobs(self, session: WeldingSession, query: WeldingQuery, *, limit: int | None = None, total: int | None = None) -> tuple[list[dict], int]:
        where, values = job_conditions(query)
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

    def parts(self, session: WeldingSession, query: WeldingQuery, *, limit: int | None = None) -> tuple[list[dict], int]:
        # State on this page belongs to the part, not to the job that establishes involvement.
        job_query = query.model_copy(update={'state': None})
        where, values = job_conditions(job_query)
        related = f'p.serial_number = j.serial_number AND {valid_serial("p.serial_number")}'
        conditions = f'EXISTS (SELECT 1 FROM weld_info_table j WHERE {where} AND {related})'
        for name in ('project_no', 'section_no', 'part_no', 'state'):
            value = query.part_state if name == 'state' else getattr(query, name)
            if value is not None and value != '':
                conditions += f' AND p.{name} = %s'
                values += (value,)
        total = session.fetch(f'SELECT COUNT(*) AS total FROM weld_part_info_table p WHERE {conditions}', values, limit=1)[0]['total']
        if limit is not None and total >= limit:
            return [], total
        size = limit if limit is not None else query.page_size
        offset = 0 if limit is not None else (query.page - 1) * size
        column = {'time': 'end_time', 'length': 'reality_lenght', 'id': 'id'}[query.sort_by]
        direction = 'DESC' if query.descending else 'ASC'
        rows = session.fetch(f'SELECT {PART_FIELDS} FROM weld_part_info_table p WHERE {conditions} ORDER BY {column} {direction}, id {direction} LIMIT %s OFFSET %s', (*values, size, offset), limit=size)
        return rows, total

    def part(self, session: WeldingSession, part_id: int) -> dict | None:
        rows = session.fetch(f'SELECT {PART_FIELDS} FROM weld_part_info_table WHERE id = %s', (part_id,), limit=1)
        return rows[0] if rows else None

    def seams(self, session: WeldingSession, part: dict, page: int, page_size: int) -> tuple[list[dict], int, bool]:
        serial = part['serial_number']
        if not serial or str(serial).strip().lower() in ('unknown', 'null'):
            return [], 0, True
        owners = session.fetch('SELECT COUNT(*) AS total FROM weld_part_info_table WHERE serial_number = %s', (serial,), limit=1)[0]['total']
        if owners != 1:
            return [], 0, True
        total = session.fetch('SELECT COUNT(*) AS total FROM welding_seam_info_table WHERE serial_number = %s', (serial,), limit=1)[0]['total']
        if page_size > 100 and total >= page_size:
            return [], total, False
        rows = session.fetch(f'SELECT {SEAM_FIELDS} FROM welding_seam_info_table WHERE serial_number = %s ORDER BY riser_no, welding_seam_no, id LIMIT %s OFFSET %s', (serial, page_size, (page - 1) * page_size), limit=page_size)
        return rows, total, False

    def source_info(self, session: WeldingSession) -> dict:
        rows = session.fetch('SELECT device_no FROM weld_info_table WHERE device_no >= 0 GROUP BY device_no ORDER BY device_no LIMIT 1001', limit=1001)
        if len(rows) > 1000:
            raise WeldingUnavailable('源库设备目录超过支持范围，请联系管理员核查')
        updated = {table: session.fetch(f'SELECT MAX(update_date) AS updated_at FROM {table}', limit=1)[0]['updated_at'] for table in TABLES}
        return {'devices': [row['device_no'] for row in rows], 'updated_at': updated}
