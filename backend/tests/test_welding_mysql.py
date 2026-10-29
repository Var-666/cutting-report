"""Opt-in integration against an isolated MySQL fixture; never a production host.

Run with WELD_TEST_MYSQL_PORT=<local-test-port> pytest backend/tests/test_welding_mysql.py.
"""
import os
from datetime import date

import pytest

from app.config import Settings
from app.database.welding import WeldingClient, WeldingRepository, WeldingUnavailable, job_source, job_aggregate, job_conditions, valid_serial, valid_time
from app.models.welding import WeldingQuery
from app.services.welding_service import WeldingService


@pytest.fixture(scope='module')
def weld_mysql():
    port = os.environ.get('WELD_TEST_MYSQL_PORT')
    if not port:
        pytest.skip('requires an explicitly started local MySQL test container')
    import mysql.connector
    # Fixed local credentials deliberately identify the disposable test instance.
    root = mysql.connector.connect(host='127.0.0.1', port=int(port), user='root', password='weld-local-test-only')
    cursor = root.cursor()
    cursor.execute('CREATE DATABASE codex_welding_test')
    cursor.execute("CREATE USER 'codex_weld_reader'@'%' IDENTIFIED BY 'weld-readonly-test-only'")
    cursor.execute('GRANT SELECT ON codex_welding_test.* TO codex_weld_reader@\'%\'')
    cursor.execute('USE codex_welding_test')
    cursor.execute('''CREATE TABLE weld_info_table (
        id INT PRIMARY KEY, serial_number CHAR(255), device_no INT, robot_no INT, riser_no INT,
        welding_seam_no INT, weld_task_serial_number INT, weld_type INT, weld_speed DOUBLE,
        weld_lenght DOUBLE, wrap_angle_lenght DOUBLE, used_time DOUBLE, start_time CHAR(255),
        end_time CHAR(255), state INT, update_date DATETIME,
        INDEX(end_time), INDEX(serial_number), INDEX(update_date))''')
    cursor.execute('''CREATE TABLE weld_part_info_table (
        id INT PRIMARY KEY, serial_number CHAR(255), project_no CHAR(255), project_name CHAR(255),
        section_no CHAR(255), part_no CHAR(255), device_no INT, theory_lenght DOUBLE,
        reality_lenght DOUBLE, weld_rate DOUBLE, arc_rate DOUBLE, used_time DOUBLE, weld_used_time DOUBLE,
        start_time CHAR(255), end_time CHAR(255), state INT, update_date DATETIME,
        INDEX(serial_number), INDEX(update_date))''')
    cursor.execute('''CREATE TABLE welding_seam_info_table (
        id INT PRIMARY KEY, serial_number CHAR(255), device_no INT, robot_no INT, riser_no INT,
        welding_seam_no INT, theory_lenght DOUBLE, reality_lenght DOUBLE, used_time DOUBLE,
        start_time CHAR(255), end_time CHAR(255), state INT, update_date DATETIME,
        INDEX(serial_number), INDEX(update_date))''')
    cursor.execute('CREATE TABLE production_stats (id INT PRIMARY KEY, update_date DATETIME)')
    cursor.execute('CREATE TABLE weld_plan_info_table (id INT PRIMARY KEY, update_date DATETIME)')
    values = [
        (1, 'p-A', 0, 0, 1, 1000, 3600, '2026-10-02 00:00:00'),
        (2, 'p-A', 0, 1, 1, 2000, 1800, '2026-10-01 23:59:59'),
        (3, 'p-B', 1, 0, 3, -50, 3600, '2026-10-01 12:00:00'),
        (4, 'Unknown', 1, 0, 99, 3000, 0, '2026-10-01 11:00:00'),
        (5, 'p-C', 1, 2, 1, 0, -1, '2026-10-01 25:00:00'),
        (6, 'p-A', 0, 0, 0, 4000, 3600, '2026-10-01 13:00:00'),
        (7, 'p-D', 1, 0, 3, None, 1800, '2026-10-01 14:00:00'),
        (8, 'p-dup', 1, 1, 2, 1000, 3600, '2026-10-01 15:00:00'),
        (9, 'bad-date', 1, 1, 1, 50000, 3600, '2026-10-32 12:00:00'),
        (10, 'bad-leap-date', 1, 1, 1, 50000, 3600, '2026-02-29 12:00:00'),
        (11, 'P-A', None, None, 1, 3000, 3600, '2026-09-30 12:00:00'),
    ]
    cursor.executemany('INSERT INTO weld_info_table (id,serial_number,device_no,robot_no,weld_type,weld_lenght,used_time,end_time,wrap_angle_lenght,state,update_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,500000,0,\'2026-10-02 01:00:00\')', values)
    cursor.executemany('INSERT INTO weld_part_info_table (id,serial_number,project_no,project_name,reality_lenght,state,update_date) VALUES (%s,%s,%s,%s,%s,%s,\'2026-10-02 01:00:00\')', [(1, 'p-A', 'P1', '项目1', 10000, 3), (2, 'p-B', 'P1', '项目1', 2000, 0), (3, 'p-dup', 'P2', '项目2', 1000, 0), (4, 'p-dup', 'P3', '项目3', 1000, 0)])
    cursor.executemany('INSERT INTO welding_seam_info_table (id,serial_number,robot_no,reality_lenght,update_date) VALUES (%s,%s,%s,%s,\'2026-10-02 01:00:00\')', [(1, 'p-A', 2, 2000), (2, 'p-A', 2, 4000), (3, 'p-dup', 2, 1000)])
    root.commit()
    cfg = Settings(weld_enabled=True, weld_db_host='127.0.0.1', weld_db_port=int(port), weld_db_name='codex_welding_test', weld_db_user='codex_weld_reader', weld_db_password='weld-readonly-test-only', weld_db_pool_size=2)
    service = WeldingService(cfg, WeldingRepository(WeldingClient(cfg)))
    yield service
    cursor.execute('DROP DATABASE codex_welding_test')
    cursor.execute("DROP USER 'codex_weld_reader'@'%'")
    cursor.close()
    root.close()


def period(**changes):
    return WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 1), **changes)


def test_real_mysql_metrics_boundaries_and_invalid_dates(weld_mysql):
    report = weld_mysql.report('daily', period())
    summary = report['summary']
    assert summary['source_rows'] == 7 and summary['job_count'] == 6
    assert summary['weld_length_m'] == 10
    assert summary['working_hours'] == 4
    assert summary['output_per_hour'] == pytest.approx(2.8)
    assert summary['involved_part_count'] == 4
    assert summary['device_count'] == 2 and summary['robot_station_count'] == 4
    assert summary['invalid_time_rows'] == 1
    assert summary['invalid_length_rows'] == 3 and summary['invalid_duration_rows'] == 2
    assert summary['wrap_length_m'] == 5 and summary['unknown_length_m'] == 3
    assert summary['completed_part_count'] is None
    assert report['items'][0]['production_date'] == '2026-10-01'
    assert sum(row['weld_length_m'] for row in report['equipment']) == 10
    month = weld_mysql.report('daily', WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 31)))
    assert month['summary']['weld_length_m'] == 11
    assert month['summary']['invalid_time_rows'] == 2
    february = weld_mysql.report('daily', WeldingQuery(start=date(2026, 2, 1), end=date(2026, 2, 28)))
    assert february['summary']['job_count'] == 0 and february['summary']['invalid_time_rows'] == 1


def test_real_mysql_filters_page_state_and_association(weld_mysql):
    report = weld_mysql.report('jobs', period(device_no=0, robot_no=0, page_size=1))
    assert report['total'] == 1 and report['items'][0]['id'] == 6
    assert report['items'][0]['weld_length_m'] == 4
    assert weld_mysql.report('jobs', period(page_size=1, page=2))['items'][0]['id'] != weld_mysql.report('jobs', period(page_size=1))['items'][0]['id']
    parts = weld_mysql.report('parts', period(state=3))
    assert parts['total'] == 1 and parts['items'][0]['id'] == 1
    assert parts['summary']['weld_length_m'] == 6
    assert parts['summary']['part_actual_length_m'] == 10
    all_parts = weld_mysql.report('parts', period())
    assert all_parts['summary']['part_actual_length_m'] is None
    assert all_parts['meta']['ambiguous_part_count'] == 1
    assert all_parts['meta']['unlinked_part_count'] == 1
    assert all(row['project_no'] != 'P2' for row in all_parts['projects'])
    assert weld_mysql.part(1)['total'] == 2
    assert weld_mysql.part(1)['items'][0]['robot_no'] == 2
    assert weld_mysql.part(3)['association_status'] == 'pending_verification'
    assert weld_mysql.part(9999) is None
    empty = weld_mysql.report('jobs', period(device_no=999))
    assert empty['total'] == 0 and empty['summary']['job_count'] == 0
    assert empty['summary']['output_per_hour'] is None


def reference_common_statistics(repository, session, query):
    """The five original SELECTs are the semantic reference for the combined query."""
    source, source_values = job_source(query)
    where, values = job_conditions(query)
    return (repository.summary(session, query), repository.daily(session, query), repository.equipment(session, query),
        session.fetch(f'SELECT j.device_no, {job_aggregate()} FROM {source} GROUP BY j.device_no ORDER BY j.device_no LIMIT 1001', source_values),
        session.fetch(f'SELECT COUNT(CASE WHEN p.owners IS NULL THEN 1 END) AS unlinked, '
            f'COUNT(CASE WHEN p.owners > 1 THEN 1 END) AS ambiguous '
            f'FROM (SELECT DISTINCT j.serial_number FROM weld_info_table j WHERE {where} '
            f'AND {valid_serial("j.serial_number")} AND {valid_time("j.end_time")}) involved '
            f'LEFT JOIN (SELECT serial_number, COUNT(*) AS owners FROM weld_part_info_table GROUP BY serial_number) p '
            f'ON p.serial_number = involved.serial_number', values)[0])


@pytest.mark.parametrize('changes', [{}, {'device_no': 0}, {'device_no': 0, 'robot_no': 0, 'weld_type': 0},
    {'anomalies_only': True}, {'serial_number': 'p-A'}, {'state': 0}, {'project_no': 'P2', 'part_state': 0},
    {'device_no': 999}, {'end': date(2026, 10, 31)}, {'start': date(2026, 2, 1), 'end': date(2026, 2, 28)},
    pytest.param({'start': date(2026, 9, 30), 'end': date(2026, 9, 30)}, id='null-dimensions')])
def test_combined_statistics_match_original_queries_and_materialize_the_source_once(weld_mysql, changes):
    import json
    from decimal import Decimal
    repository = weld_mysql.repository
    query = WeldingQuery.model_validate({**period().model_dump(), **changes})
    with repository.client.snapshot() as session:
        expected = reference_common_statistics(repository, session, query)
        captured = []
        class RecordingSession:
            def fetch(self, sql, values=(), **kwargs):
                captured.append((sql, values))
                return session.fetch(sql, values, **kwargs)
        actual = repository.statistics(RecordingSession(), query)
        assert len(captured) == 1
        for old, new in zip(expected, actual, strict=True):
            old_rows, new_rows = ([old], [new]) if isinstance(old, dict) else (old, new)
            assert len(old_rows) == len(new_rows)
            for old_row, new_row in zip(old_rows, new_rows, strict=True):
                assert old_row.keys() == new_row.keys()
                for key, value in old_row.items():
                    if isinstance(value, (int, float, Decimal)):
                        assert float(new_row[key]) == pytest.approx(float(value)), key
                    else:
                        assert new_row[key] == value, key
        sql, values = captured[0]
        plan = json.loads(session.fetch('EXPLAIN FORMAT=JSON ' + sql, values)[0]['EXPLAIN'])
        def source_nodes(node):
            if isinstance(node, dict):
                return int(node.get('table_name') == 'source_job') + sum(source_nodes(value) for value in node.values())
            return sum(source_nodes(value) for value in node) if isinstance(node, list) else 0
        assert source_nodes(plan) == 1, 'all aggregates must share one materialization of the source interval'


@pytest.mark.parametrize('changes', [{}, {'device_no': 0}, {'part_state': 0}, {'part_state': 3},
    {'project_no': 'P2'}, {'project_no': 'P3'}, {'serial_number': 'p-A', 'page': 2, 'page_size': 1},
    {'serial_number': 'absent'}, {'sort_by': 'length', 'descending': False},
    {'end': date(2026, 10, 31)}, {'start': date(2026, 2, 1), 'end': date(2026, 2, 28)}])
def test_part_scope_matches_original_project_totals_and_page_queries(weld_mysql, changes):
    import json
    from app.database.welding import PART_FIELDS
    repository = weld_mysql.repository
    query = WeldingQuery.model_validate({**period().model_dump(), **changes})
    where, values = job_conditions(query)
    conditions, part_values = repository.part_conditions(query)
    with repository.client.snapshot() as session:
        projects = session.fetch(f'SELECT p.project_no, MIN(p.project_name) AS project_name, COUNT(*) AS part_count, '
            f'SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END) / 1000 AS cumulative_actual_length_m '
            f'FROM weld_part_info_table p WHERE {valid_serial("p.serial_number")} '
            f'AND (SELECT COUNT(*) FROM weld_part_info_table owner WHERE owner.serial_number=p.serial_number)=1 '
            f'AND EXISTS (SELECT 1 FROM weld_info_table j WHERE {where} AND {valid_time("j.end_time")} AND j.serial_number=p.serial_number) '
            f'GROUP BY p.project_no ORDER BY part_count DESC,p.project_no LIMIT 101', values)
        totals = session.fetch(f'SELECT COUNT(*) AS part_row_count, '
            f'COALESCE(SUM(CASE WHEN p.reality_lenght >= 0 THEN p.reality_lenght ELSE 0 END),0)/1000 AS part_actual_length_m, '
            f'COALESCE(SUM((SELECT COUNT(*) FROM weld_part_info_table owner WHERE owner.serial_number=p.serial_number)>1),0) AS ambiguous_part_rows, '
            f'COALESCE(SUM(CASE WHEN p.used_time<0 OR p.weld_used_time<0 OR p.weld_used_time>p.used_time OR p.weld_rate<0 '
            f'OR p.weld_rate>1 OR p.arc_rate<0 OR p.arc_rate>1 THEN 1 ELSE 0 END),0) AS invalid_part_rows '
            f'FROM weld_part_info_table p WHERE {conditions}', part_values)[0]
        if totals['ambiguous_part_rows']:
            totals['part_actual_length_m'] = None
        captured = []
        class RecordingSession:
            def fetch(self, sql, values=(), **kwargs):
                captured.append((sql, values))
                return session.fetch(sql, values, **kwargs)
        actual_projects, actual_totals = repository.part_statistics(RecordingSession(), query)
        assert actual_projects == projects
        assert actual_totals == totals
        assert len(captured) == 1
        sql, bound = captured[0]
        plan = json.loads(session.fetch('EXPLAIN FORMAT=JSON '+sql, bound)[0]['EXPLAIN'])
        def physical_nodes(node, alias):
            if isinstance(node, dict):
                return int(node.get('table_name') == alias) + sum(physical_nodes(value, alias) for value in node.values())
            return sum(physical_nodes(value, alias) for value in node) if isinstance(node, list) else 0
        visits = (physical_nodes(plan, 'part_job'), physical_nodes(plan, 'source_part'))
        assert all(count <= 1 for count in visits)
        if totals['part_row_count']:
            assert visits == (1, 1)
        column = {'time': 'end_time', 'length': 'reality_lenght', 'id': 'id'}[query.sort_by]
        direction = 'DESC' if query.descending else 'ASC'
        expected = session.fetch(f'SELECT {PART_FIELDS} FROM weld_part_info_table p WHERE {conditions} '
            f'ORDER BY {column} {direction},id {direction} LIMIT %s OFFSET %s',
            (*part_values, query.page_size, (query.page-1)*query.page_size))
        rows, total = repository.parts(session, query, total=totals['part_row_count'])
        assert rows == expected and total == totals['part_row_count']


def test_real_mysql_snapshot_is_repeatable_and_account_is_readonly(weld_mysql):
    import mysql.connector
    writer = mysql.connector.connect(host='127.0.0.1', port=weld_mysql.settings.weld_db_port, user='root', password='weld-local-test-only', database='codex_welding_test')
    cursor = writer.cursor()
    try:
        with weld_mysql.repository.client.snapshot() as session:
            before = session.fetch('SELECT weld_lenght FROM weld_info_table WHERE id=1', limit=1)[0]['weld_lenght']
            cursor.execute('UPDATE weld_info_table SET weld_lenght=1500 WHERE id=1')
            writer.commit()
            assert session.fetch('SELECT weld_lenght FROM weld_info_table WHERE id=1', limit=1)[0]['weld_lenght'] == before
        with weld_mysql.repository.client.snapshot() as session:
            assert session.fetch('SELECT weld_lenght FROM weld_info_table WHERE id=1', limit=1)[0]['weld_lenght'] == 1500
    finally:
        cursor.execute('UPDATE weld_info_table SET weld_lenght=1000 WHERE id=1')
        writer.commit()
        cursor.close()
        writer.close()
    with weld_mysql.repository.client.snapshot() as session:
        with pytest.raises(mysql.connector.Error) as denied:
            session.fetch('DELETE FROM weld_info_table WHERE id=1')
        assert denied.value.errno in (1142, 1792)
    assert weld_mysql.report('jobs', period())['total'] == 7


def test_real_mysql_timeout_recovers_for_next_request(weld_mysql, caplog):
    from dataclasses import replace
    client = WeldingClient(replace(weld_mysql.settings, weld_db_query_timeout=1))
    previous_pool = client._get_pool()
    with pytest.raises(WeldingUnavailable):
        with client.snapshot() as session:
            session.fetch('SELECT SLEEP(2) AS wait_result', limit=1)
    assert 'type=ReadTimeoutError' in caplog.text
    assert client._pool is None
    with client.snapshot() as session:
        assert session.fetch('SELECT 1 AS healthy', limit=1) == [{'healthy': 1}]
    assert client._pool is not previous_pool
    service = WeldingService(client.settings, WeldingRepository(client))
    assert service.report('daily', period())['summary']['weld_length_m'] == 10


def test_real_mysql_api_exports_and_pdf(weld_mysql):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.auth import require_admin
    from app.api.welding import router
    from app.api.database import router as database_router
    from app.services.pdf_service import PdfService
    app = FastAPI()
    app.include_router(router)
    app.include_router(database_router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    app.state.welding = weld_mysql
    app.state.settings = weld_mysql.settings
    app.state.pdf_service = PdfService(operation_timeout_seconds=60)
    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'welding-mysql-test'
        return await call_next(request)
    client = TestClient(app)
    tested = client.post('/api/v1/database/welding/test-connection', json={'host': '127.0.0.1', 'port': weld_mysql.settings.weld_db_port, 'database': 'codex_welding_test', 'username': 'codex_weld_reader'})
    assert tested.status_code == 200, tested.text
    for path in ('statistics/daily', 'parts', 'jobs', 'equipment/summary'):
        response = client.get(f'/api/v1/welding/{path}?start=2026-10-01&end=2026-10-01')
        assert response.status_code == 200, response.text
        assert response.json()['data']['summary']['weld_length_m'] == 10
    for format in ('csv', 'html', 'pdf'):
        result = client.get(f'/api/v1/welding/export?report_type=daily&format={format}&start=2026-10-01&end=2026-10-01')
        assert result.status_code == 200, result.text[:500]
        assert 'attachment' in result.headers['content-disposition']
        if format == 'pdf':
            assert result.content.startswith(b'%PDF')
        else:
            assert '每日生产统计' in result.content.decode('utf-8-sig')


def test_source_audit_and_timing_script_against_local_fixture(weld_mysql, monkeypatch, capsys):
    import json
    from pathlib import Path
    import runpy
    import sys
    script = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts' / 'check_welding.py'))
    main = script['main']
    monkeypatch.setitem(main.__globals__, 'Settings', lambda: weld_mysql.settings)
    monkeypatch.setattr(sys, 'argv', ['check_welding.py', '--end', '2026-10-01', '--runs', '1'])
    assert main() == 0
    output = capsys.readouterr().out
    audit = json.loads(output)
    assert len(audit['timing']) == 12
    assert audit['duplicate_part_serial_groups'] == 1
    assert not audit['account_requires_grant_review']
    assert audit['tables']['weld_info_table']['total'] == 11
    assert 'weld-readonly-test-only' not in output and 'codex_weld_reader' not in output
