from collections import Counter
from contextlib import contextmanager
from datetime import date

import pytest

from app.config import Settings
from app.database.welding import WeldingRepository
from app.models.welding import WeldingQuery
from app.services.welding_service import WeldingService


class Repository(WeldingRepository):
    def __init__(self):
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


@pytest.mark.parametrize('kind', ['jobs', 'parts'])
def test_paging_and_sorting_only_read_source_revision_and_details(kind):
    repo = Repository()
    service = WeldingService(Settings(), repo)
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
    service = WeldingService(Settings(), repo)
    part = service.report('parts', query())
    daily = service.report('daily', query())
    service.report('jobs', query())
    service.report('equipment', query())
    assert part['summary']['part_row_count'] == 3
    assert 'part_row_count' not in daily['summary']
    assert repo.calls['summary'] == 1


def test_filters_source_changes_expiry_exports_and_service_replacement_recompute(monkeypatch):
    clock = [0]
    monkeypatch.setattr('app.services.welding_service.monotonic', lambda: clock[0])
    repo = Repository()
    service = WeldingService(Settings(), repo)
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
    WeldingService(Settings(), repo).report('jobs', query())
    assert repo.calls['summary'] == 7


def test_missing_watermark_and_failures_are_not_cached():
    repo = Repository()
    service = WeldingService(Settings(), repo)
    repo.fail = True
    with pytest.raises(ValueError, match='source failed'):
        service.report('jobs', query())
    assert not service._statistics
    repo.fail = False
    repo.revision = None
    service.report('jobs', query())
    service.report('jobs', query(page=2))
    assert repo.calls['summary'] == 3
    assert not service._statistics


def test_statistics_cache_is_bounded():
    service = WeldingService(Settings(), Repository())
    for i in range(33):
        service._cached_statistics(('common', str(i)), lambda: {})
    assert len(service._statistics) == 32
    assert ('common', '0') not in service._statistics


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
