"""Exercise the generated SELECTs on isolated data; not a MySQL performance benchmark."""
import calendar
import re
import sqlite3
from datetime import date

import pytest

from app.config import Settings
from app.database.welding import WeldingRepository
from app.models.welding import WeldingQuery
from app.services.welding_service import WeldingService


@pytest.fixture
def project_statistics(tmp_path):
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    connection.create_function('CHAR_LENGTH', 1, lambda value: len(value) if value is not None else None)
    connection.create_function('regexp', 2, lambda pattern, value: bool(re.search(pattern, value)) if value is not None else None)
    connection.create_function('LEFT', 2, lambda value, length: value[:length] if value is not None else None)
    connection.create_function('CONCAT', -1, lambda *values: ''.join(map(str, values)) if None not in values else None)
    def last_day(value):
        parsed = date.fromisoformat(value)
        return parsed.replace(day=calendar.monthrange(parsed.year, parsed.month)[1]).isoformat()
    connection.create_function('LAST_DAY', 1, last_day)
    connection.create_function('DAY', 1, lambda value: int(value[-2:]))
    connection.executescript('''
        CREATE TABLE weld_info_table (
            serial_number TEXT COLLATE NOCASE, device_no INT, robot_no INT, weld_type INT,
            state INT, end_time TEXT, weld_lenght REAL, used_time REAL);
        CREATE TABLE weld_part_info_table (
            id INT, serial_number TEXT COLLATE NOCASE, project_no TEXT, project_name TEXT,
            section_no TEXT, part_no TEXT, state INT, reality_lenght REAL,
            used_time REAL, weld_used_time REAL, weld_rate REAL, arc_rate REAL,
            device_no INT, theory_lenght REAL, start_time TEXT, end_time TEXT, update_date TEXT);
    ''')
    parts = [('A', 'P1', 2000, 0), ('B', 'P1', 3000, 3), ('C', 'P2', 4000, 0),
             ('D', 'P2', 1000, 0), ('D', 'P3', 2000, 3), ('unknown', 'P1', 100000, 0),
             ('E', 'P4', 10000, 0), ('F', 'P5', 5000, 0), ('G', 'P6', -1000, 0)]
    connection.executemany('INSERT INTO weld_part_info_table (id, serial_number, project_no, project_name, section_no, part_no, state, reality_lenght, used_time, weld_used_time, weld_rate, arc_rate) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        [(index, serial, project, project, 'section', f'part-{index}', state, length,
          -1 if serial == 'G' else 10, 5, .5, .5) for index, (serial, project, length, state) in enumerate(parts, 1)])
    jobs = [('A', 0, '2026-10-28 10:00:00'), ('A', 0, '2026-10-28 11:00:00'),
            ('B', 1, '2026-10-28 10:00:00'), ('C', 1, '2026-10-28 10:00:00'),
            ('D', 0, '2026-10-28 10:00:00'), ('unknown', 0, '2026-10-28 10:00:00'),
            ('E', 0, '2026-10-28 25:00:00'), ('F', 0, '2026-10-27 10:00:00'),
            ('G', 0, '2026-10-28 10:00:00')]
    connection.executemany('INSERT INTO weld_info_table VALUES (?, ?, 0, 1, 0, ?, 1000, 10)', jobs)
    class Session:
        queries = []
        def fetch(self, sql, values=(), **kwargs):
            self.queries.append(sql)
            # SQLite needs quoted LEFT and positional placeholders; all joins/predicates are unchanged.
            sql = sql.replace('LEFT(', '"LEFT"(').replace('%s', '?').replace('LIMIT 18446744073709551615', 'LIMIT -1')
            return [dict(row) for row in connection.execute(sql, values).fetchall()]
    session = Session()
    service = WeldingService(Settings(project_root=tmp_path), WeldingRepository(None))
    try:
        yield service, session
    finally:
        connection.close()


@pytest.mark.parametrize(('filters', 'expected_projects', 'count', 'ambiguous', 'length', 'invalid'), [
    ({}, [('P1', 2, 5), ('P2', 1, 4), ('P6', 1, 0)], 7, 2, None, 1),
    ({'project_no': 'P1'}, [('P1', 2, 5)], 2, 0, 5, 0),
    ({'project_no': 'P2'}, [('P2', 1, 4)], 2, 1, None, 0),
    ({'part_state': 0, 'device_no': 0}, [('P1', 1, 2), ('P6', 1, 0)], 4, 1, None, 1),
    ({'serial_number': 'absent'}, [], 0, 0, 0, 0),
])
def test_project_statistics_preserve_unique_ownership_filters_and_units(project_statistics, filters, expected_projects, count, ambiguous, length, invalid):
    service, session = project_statistics
    query = WeldingQuery(start=date(2026, 10, 28), end=date(2026, 10, 28), **filters)
    projects, speeds, totals = service._report_statistics(session, 'parts', query)
    assert [(row['project_no'], row['part_count'], row['cumulative_actual_length_m']) for row in projects] == expected_projects
    assert totals == {'part_row_count': count, 'ambiguous_part_rows': ambiguous, 'part_actual_length_m': length, 'invalid_part_rows': invalid}
    assert speeds == []
    assert len(session.queries) == 1
    assert all('FROM weld_part_info_table owner WHERE' not in sql for sql in session.queries)
    assert 'FROM weld_info_table part_job' in session.queries[0]
