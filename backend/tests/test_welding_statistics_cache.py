from collections import Counter
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from tests.line_fixtures import welding_settings
from app.database.welding import WeldingRepository
from app.models.welding import WeldingQuery
from app.services.welding_service import WeldingService


class Repository(WeldingRepository):
    def __init__(self):
        self.device_nos = ()
        self.client = self
        self.calls = Counter()
        self.revision = '2026-10-27 10:00:00'
        self.fail = False

    @contextmanager
    def snapshot(self):
        yield self

    def fetch(self, sql, *args, **kwargs):
        self.calls['sql'] += 1
        if 'AS unlinked' in sql:
            return [{'unlinked': 0, 'ambiguous': 0}]
        if 'AS part_row_count' in sql:
            return [{'part_row_count': 3, 'ambiguous_part_rows': 0, 'invalid_part_rows': 0, 'part_actual_length_m': 2}]
        return []

    def source_info(self, session):
        self.calls['source'] += 1
        return {'devices': [0], 'updated_at': {'jobs': self.revision, 'empty_seams': None}}

    def summary(self, session, query):
        self.calls['summary'] += 1
        if self.fail:
            raise ValueError('source failed')
        return {'source_rows': 3, 'job_count': 3, **dict.fromkeys([
            'invalid_time_rows', 'invalid_serial_rows', 'invalid_length_rows',
            'invalid_duration_rows', 'unverified_state_rows'], 0)}

    def daily(self, *args):
        self.calls['daily'] += 1
        return []

    def equipment(self, *args):
        self.calls['equipment'] += 1
        return []

    def statistics(self, session, query):
        return self.summary(session, query), self.daily(session, query), self.equipment(session, query), [], {'unlinked': 0, 'ambiguous': 0}

    def part_statistics(self, session, query):
        return [], {'part_row_count': 3, 'ambiguous_part_rows': 0, 'invalid_part_rows': 0, 'part_actual_length_m': 2}

    def jobs(self, session, query, *, limit, total):
        self.calls['jobs'] += 1
        assert total == 3
        return [{'id': query.page, 'serial_number': 'part'}], total

    def parts(self, session, query, *, limit, total):
        self.calls['parts'] += 1
        assert total == 3
        return [{'id': query.page, 'serial_number': 'part'}], total


def query(**kwargs):
    return WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 2), **kwargs)


@pytest.fixture
def aggregate_groups():
    daily = [{'production_date': f'2026-10-0{day}', 'weld_length_m': length}
             for day, length in ((1, 4), (2, None), (3, 0), (4, 4))]
    equipment = [{'device_no': device, 'robot_no': robot, 'weld_length_m': length}
                 for device, robot, length in ((None, None, 4), (None, 0, 4), (0, None, 4), (0, 0, 4),
                                               (0, 2, None), (2, None, 0), (2, 0, 4), (2, 3, 4))]
    return daily, equipment


@pytest.mark.parametrize('kind, sort_by', [('equipment', 'time'), ('equipment', 'id'), ('equipment', 'length'),
                                         ('daily', 'time'), ('daily', 'length')])
@pytest.mark.parametrize('descending', [False, True])
def test_aggregate_sorting_keeps_null_zero_identity_and_page_order(aggregate_groups, kind, sort_by, descending):
    daily, equipment = aggregate_groups
    expected = equipment if kind == 'equipment' else daily
    if sort_by == 'length':
        indices = (4, 5, 0, 1, 2, 3, 6, 7) if kind == 'equipment' else (1, 2, 0, 3)
        expected = [expected[index] for index in indices]
    if descending:
        expected = list(reversed(expected))
    selected = query(sort_by=sort_by, descending=descending, page=2, page_size=2)
    # Equal lengths still use the group identity, independent of source row order.
    for days, stations in ((daily, equipment), (list(reversed(daily)), list(reversed(equipment)))):
        original = (list(days), list(stations))
        rows, total = WeldingService._aggregate_rows(kind, selected, days, stations)
        assert rows == expected[2:4] and total == len(expected)
        rows, _ = WeldingService._aggregate_rows(kind, selected, days, stations, limit=100)
        assert rows == expected
        assert (days, stations) == original


def test_repository_statistics_and_page_sort_use_the_same_null_group_order(aggregate_groups):
    daily, equipment = aggregate_groups
    def fetch(sql, values, **kwargs):
        return [{'bucket': 'summary'}, {'bucket': 'association', 'unlinked': 0, 'ambiguous': 0},
                *[{'bucket': 'daily', **row} for row in reversed(daily)],
                *[{'bucket': 'equipment', **row} for row in reversed(equipment)],
                *[{'bucket': 'devices', 'device_no': device} for device in (2, 0, None)]]
    _, days, stations, devices, _ = WeldingRepository(None).statistics(SimpleNamespace(fetch=fetch), query())
    assert days == daily and stations == equipment
    assert [row['device_no'] for row in devices] == [None, 0, 2]
    rows, total = WeldingService._aggregate_rows('equipment', query(descending=False, page_size=100), days, stations)
    assert rows == equipment and total == len(equipment)


@pytest.mark.parametrize('kind', ['jobs', 'parts'])
def test_paging_and_sorting_only_read_source_revision_and_details(kind):
    repo = Repository()
    service = WeldingService(welding_settings(), repo)
    first = service.report(kind, query())
    sql = repo.calls['sql']
    first['summary']['job_count'] = -1
    second = service.report(kind, query(page=2, page_size=1, sort_by='length', descending=False))
    assert second['items'][0]['id'] == 2
    assert second['summary']['job_count'] == 3
    assert first['data_version'] == second['data_version']
    assert repo.calls['summary'] == repo.calls['daily'] == repo.calls['equipment'] == 1
    assert repo.calls[kind] == repo.calls['source'] == 2
    assert repo.calls['sql'] == sql


def test_common_statistics_are_shared_across_report_types_without_part_fields_leaking():
    repo = Repository()
    service = WeldingService(welding_settings(), repo)
    part = service.report('parts', query())
    daily = service.report('daily', query())
    service.report('jobs', query())
    service.report('equipment', query())
    assert part['summary']['part_row_count'] == 3
    assert 'part_row_count' not in daily['summary']
    assert repo.calls['summary'] == 1


def test_filters_source_changes_expiry_exports_and_service_replacement_recompute(monkeypatch):
    clock = [0]
    monkeypatch.setattr('app.services.query_cache.monotonic', lambda: clock[0])
    repo = Repository()
    service = WeldingService(welding_settings(), repo)
    service.report('jobs', query())
    service.report('jobs', query(device_no=0))
    assert repo.calls['summary'] == 2
    repo.revision = '2026-10-27 10:01:00'
    service.report('jobs', query())
    assert repo.calls['summary'] == 3
    clock[0] = 30
    service.report('jobs', query())
    assert repo.calls['summary'] == 4
    service.report('jobs', query(), export_limit=100)
    service.report('jobs', query(), export_limit=100)
    assert repo.calls['summary'] == 6
    WeldingService(welding_settings(), repo).report('jobs', query())
    assert repo.calls['summary'] == 7


def test_missing_watermark_and_failures_are_not_cached():
    repo = Repository()
    service = WeldingService(welding_settings(), repo)
    repo.fail = True
    with pytest.raises(ValueError, match='source failed'):
        service.report('jobs', query())
    assert not service._statistics._entries
    repo.fail = False
    repo.revision = None
    service.report('jobs', query())
    service.report('jobs', query(page=2))
    assert repo.calls['summary'] == 3
    assert not service._statistics._entries


def test_statistics_cache_is_bounded():
    service = WeldingService(welding_settings(), Repository())
    for i in range(33):
        service._cached_statistics(('common', str(i)), lambda: {})
    assert len(service._statistics._entries) == 32
    assert all(key[-2:] != ('common', '0') for key in service._statistics._entries)


def test_parts_reuse_cached_count_with_the_same_bound_predicate():
    calls = []
    class Session:
        def fetch(self, sql, values, **kwargs):
            calls.append((sql, values))
            return [{'total': 3}] if '\nSELECT COUNT(*)' in sql else [{'id': 2}]
    repo = WeldingRepository(None)
    selected = query(page=2, page_size=1, part_state=0, project_no='project')
    assert repo.parts(Session(), selected, total=3) == ([{'id': 2}], 3)
    assert len(calls) == 1 and 'p.state = %s' in calls[0][0]
    assert calls[0][1][-3:] == (0, 1, 1)
    calls.clear()
    assert repo.parts(Session(), selected) == ([{'id': 2}], 3)
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1][:-2]
