"""Check serialized core responses against the fields consumed by the frontend.

External production sources are replaced; routes, services and JSON conversion run.
"""
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth, leadership, welding
from app.config import Settings
from app.database.welding import JOB_FIELDS, PART_FIELDS, SEAM_FIELDS, WeldingRepository
from app.database.workshop import WorkshopRepository
from app.services.leadership_report_service import LeadershipReportService
from app.services.welding_service import WeldingService


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path, data_source_mode='demo', demo_mode=True,
                   weld_enabled=True, worker_database_auth_enabled=False, auth_cookie_secure=False)
    cfg.ensure_runtime_dirs()
    users = WorkshopRepository(tmp_path / 'contracts.db')
    users.initialize()
    app = FastAPI()
    for router in (auth.router, leadership.router, welding.router):
        app.include_router(router)
    app.state.settings = cfg
    app.state.workshop = users
    app.state.devices = {'Cutter01': SimpleNamespace(settings=cfg, leadership_report_service=LeadershipReportService(cfg))}
    app.dependency_overrides[auth.require_admin] = lambda: {'role': 'admin'}
    monkeypatch.setattr(auth, 'settings', cfg)

    @app.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'response-contract'
        return await call_next(request)

    with TestClient(app) as session:
        yield session


def data(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['success'] is True
    assert isinstance(body['request_id'], str)
    assert isinstance(body['data'], dict)
    return body['data']


def assert_user(user):
    for key in ('user_id', 'username', 'display_name'):
        assert isinstance(user[key], str)
    assert user['role'] in ('admin', 'worker')
    assert type(user['active']) is bool
    assert not {'password', 'password_hash', 'session_token'} & user.keys()


def test_authentication_response_contract(client):
    status = data(client.get('/api/v1/auth/status'))
    assert status['setup_required'] is True
    assert status['worker_database_auth_enabled'] is False
    assert status['user'] is None
    credentials = {'username': 'contract-admin', 'password': 'contract-password'}
    for path, payload in [('bootstrap', {**credentials, 'display_name': '管理员'}), ('login', credentials)]:
        result = data(client.post(f'/api/v1/auth/{path}', json=payload))
        assert_user(result['user'])
        assert isinstance(result['expires_at'], str)
        assert client.cookies.get('cutting_session')
    status = data(client.get('/api/v1/auth/status'))
    assert status['setup_required'] is False
    assert_user(status['user'])
    assert data(client.post('/api/v1/auth/logout'))['logged_out'] is True
    assert data(client.get('/api/v1/auth/status'))['user'] is None


def test_cutting_report_response_contract(client):
    report = data(client.get('/api/v1/leadership-report?device_id=Cutter01&report_month=2026-10'))
    assert report['report_month'] == '2026-10-01'
    assert isinstance(report['source_mode'], str)
    for rows in (report['daily_records'], report['monthly_records'],
                 [report['current_month_total'], report['twelve_month_total']]):
        assert rows
        for row in rows:
            assert isinstance(row['period_start'], str)
            assert type(row['task_count']) is int
            for key in ('cut_length_m', 'groove_length_m', 'work_time_seconds'):
                assert type(row[key]) in (int, float)
    assert isinstance(report['project_distribution'], list)
    assert isinstance(report['snapshot'], dict)
    assert isinstance(report['analysis_source_version'], str)


class WeldingSource(WeldingRepository):
    """Repository-shaped source with nullable details and Decimal aggregates."""
    def __init__(self, empty=False):
        self.client = self
        self.empty = empty
        self.metrics = {key: 0 for key in ('source_rows', 'job_count', 'involved_part_count', 'device_count',
                        'robot_station_count', 'invalid_time_rows', 'invalid_serial_rows', 'invalid_length_rows',
                        'invalid_duration_rows', 'unverified_state_rows', 'flat_wrap_length_m', 'flat_length_m',
                        'vertical_wrap_length_m', 'vertical_length_m', 'unknown_length_m')}
        self.metrics.update(weld_length_m=Decimal('2.5'), working_hours=Decimal('1'), output_per_hour=None)
        self.raw = dict(id=1, serial_number=None, device_no=0, robot_no=None, project_no=None,
                        project_name=None, section_no=None, part_no=None, riser_no=None, welding_seam_no=None,
                        weld_type=1, weld_speed=None, weld_lenght=None, theory_lenght=None,
                        reality_lenght=2500, used_time=None, weld_rate=None, arc_rate=None,
                        start_time=None, end_time=None)

    @contextmanager
    def snapshot(self):
        yield self

    def fetch(self, sql, *args, **kwargs):
        if 'AS unlinked' in sql:
            return [{'unlinked': 0, 'ambiguous': 0}]
        if 'AS part_actual_length_m' in sql:
            return [{'part_row_count': 0 if self.empty else 1, 'part_actual_length_m': None, 'ambiguous_part_rows': 0, 'invalid_part_rows': 0}]
        if 'GROUP BY p.project_no' in sql:
            return []
        if 'speed_band' in sql:
            return []
        return [] if self.empty else [{'device_no': 0, **self.metrics}]

    def summary(self, *_):
        return self.metrics

    def daily(self, *_):
        return [] if self.empty else [{'production_date': '2026-10-01', **self.metrics}]

    def equipment(self, *_):
        return [] if self.empty else [{'device_no': 0, 'robot_no': None, **self.metrics}]

    def statistics(self, session, query):
        devices = [] if self.empty else [{'device_no': 0, **self.metrics}]
        return self.summary(session, query), self.daily(session, query), self.equipment(session, query), devices, {'unlinked': 0, 'ambiguous': 0}

    def part_statistics(self, session, query):
        return [], {'part_row_count': 0 if self.empty else 1, 'part_actual_length_m': None, 'ambiguous_part_rows': 0, 'invalid_part_rows': 0}

    def source_info(self, *_):
        return {'devices': [0], 'updated_at': {'weld_info_table': None}}

    def jobs(self, *_, **kwargs):
        return ([], 0) if self.empty else ([{key: self.raw.get(key) for key in JOB_FIELDS.split(', ')}], 1)

    def parts(self, *_, **kwargs):
        return ([], 0) if self.empty else ([self.part()], 1)

    def part(self, *_):
        return {key: self.raw.get(key) for key in PART_FIELDS.split(', ')}

    def seams(self, *_):
        return [{key: self.raw.get(key) for key in SEAM_FIELDS.split(', ')}], 1, False


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('kind,path', [('daily', 'statistics/daily'), ('equipment', 'equipment/summary'),
                                      ('jobs', 'jobs'), ('parts', 'parts'), ('jobs', 'dashboard')])
def test_welding_report_response_contract(client, kind, path, empty):
    client.app.state.welding = WeldingService(client.app.state.settings, WeldingSource(empty))
    report = data(client.get(f'/api/v1/welding/{path}?start=2026-10-01&end=2026-10-01'))
    assert report['report_type'] == kind
    assert report['query']['start'] == report['query']['end'] == '2026-10-01'
    assert report['query']['sort_by'] in ('time', 'length', 'id')
    assert type(report['total']) is int
    assert isinstance(report['items'], list)
    assert len(report['items']) == (0 if empty else 1)
    assert isinstance(report['summary']['weld_length_m'], (int, float))
    assert report['summary']['completed_part_count'] is None
    assert len(report['data_version']) == 64
    assert isinstance(report['meta']['calculated_at'], str)
    assert isinstance(report['meta']['data_updated_at'], dict)
    for key in ('pending_metrics', 'quality'):
        assert isinstance(report['meta'][key], dict)
    assert isinstance(report['meta']['notices'], list)
    assert (report['today'] is not None) == (path == 'dashboard')
    if empty:
        assert report['daily'] == report['equipment'] == report['device_distribution'] == []
        return
    assert 'id' not in report['daily'][0] and 'device_no' not in report['daily'][0]
    assert 'production_date' not in report['equipment'][0]
    assert report['equipment'][0]['robot_no'] is None
    assert 'robot_no' not in report['device_distribution'][0]
    row = report['items'][0]
    if kind in ('jobs', 'parts'):
        assert type(row['id']) is int
        assert row['serial_number'] is None
        assert row['working_hours'] is None
        assert isinstance(row['state_label'], str)
        assert isinstance(row['quality_reasons'], list)
        assert row['weld_length_m' if kind == 'jobs' else 'theoretical_length_m'] is None
        if kind == 'parts':
            assert 'robot_no' not in row
    else:
        assert 'id' not in row
        assert type(row['weld_length_m']) in (int, float)
        assert row['output_per_hour'] is None


def test_welding_part_response_contract(client):
    client.app.state.welding = WeldingService(client.app.state.settings, WeldingSource())
    result = data(client.get('/api/v1/welding/parts/1'))
    assert type(result['part']['id']) is int
    assert result['part']['serial_number'] is None
    assert result['part']['theoretical_length_m'] is None
    assert isinstance(result['part']['actual_length_m'], (int, float))
    assert type(result['total']) is int
    assert result['association_status'] == 'exact_serial_match'
    assert isinstance(result['notice'], str)
    seam = result['items'][0]
    assert type(seam['id']) is int
    assert seam['robot_no'] is None
    assert seam['theoretical_length_m'] is None
    assert seam['working_hours'] is None
    assert isinstance(seam['quality_reasons'], list)


@pytest.mark.parametrize('path', ['statistics/daily', 'equipment/summary', 'jobs', 'parts', 'dashboard'])
@pytest.mark.parametrize('dates', ['start=2026-10-01', 'end=2026-10-01', ''])
def test_welding_report_requires_both_dates(client, path, dates):
    assert client.get(f'/api/v1/welding/{path}?{dates}').status_code == 422
