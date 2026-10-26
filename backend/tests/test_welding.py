from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.auth import require_admin
from app.api.welding import router
from app.api.production_lines import router as lines_router
from app.config import Settings
from app.database.welding import WeldingClient, WeldingRepository, WeldingUnavailable, job_conditions, job_aggregate
from app.models.welding import WeldingQuery
from app.services.database_runtime import parse_database_settings
from app.services.llm_service import analysis_system_prompt
from app.services.welding_export import WeldingExport, csv_bytes
from app.services.welding_service import WeldingService, mapped_row, summary_values


def query(**kwargs):
    return WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 31), **kwargs)


def test_interval_and_pagination_validation():
    assert query().bounds == ('2026-10-01 00:00:00', '2026-11-01 00:00:00')
    for changes in [{'page_size': 101}, {'page': 0}, {'robot_no': -1}, {'device_no': -1},
                    {'end': date(2026, 9, 1)}, {'end': date(2027, 10, 2)}, {'unknown': 'field'}]:
        with pytest.raises(ValidationError):
            WeldingQuery.model_validate({**query().model_dump(), **changes})


def test_parameters_are_bound_and_zero_device_robot_are_preserved():
    value = "project' OR 1=1 --"
    where, params = job_conditions(query(device_no=0, robot_no=0, project_no=value, part_state=3))
    assert value not in where and value in params
    assert 'j.device_no = %s' in where and 'j.robot_no = %s' in where
    assert params[2:4] == (0, 0)
    assert 'EXISTS' in where and 'JOIN' not in where and 'p.state = %s' in where
    aggregate = job_aggregate()
    assert 'state = 1' not in aggregate
    assert 'wrap_angle_lenght' not in aggregate
    assert 'NULLIF' in aggregate and 'COUNT(DISTINCT' in aggregate


@pytest.mark.parametrize('code,label', [(0, '平焊包角'), (1, '平焊'), (2, '立焊包角'), (3, '立焊'), (8, '未知类型（8）')])
def test_units_types_unknown_state_and_anomalies(code, label):
    row = mapped_row({'id': 1, 'weld_type': code, 'state': 3, 'serial_number': 'Unknown', 'weld_lenght': 2013.659122,
                      'used_time': 3600, 'start_time': '2026-10-01 00:00:00', 'end_time': 'bad'}, 'jobs')
    assert row['weld_length_m'] == pytest.approx(2.013659122)
    assert row['working_hours'] == 1
    assert row['state_label'] == '待确认（3）'
    assert row['weld_type_label'] == label
    assert '流水号无效' in row['quality_reasons'] and '结束时间无效' in row['quality_reasons']
    assert mapped_row({'weld_lenght': None}, 'jobs')['weld_length_m'] is None
    assert mapped_row({'weld_lenght': -1}, 'jobs')['weld_length_m'] is None
    summary = summary_values({'flat_wrap_length_m': 1, 'vertical_wrap_length_m': 2})
    assert summary['wrap_length_m'] == 3 and summary['completed_part_count'] is None


def test_connection_is_lazy_and_disabled_does_not_import_or_connect():
    client = WeldingClient(Settings(weld_enabled=False))
    assert client._pool is None
    with pytest.raises(WeldingUnavailable, match='尚未启用'):
        with client.snapshot():
            pytest.fail('disabled client must not yield')


def test_outer_query_budget_exhaustion_does_not_start_another_sql():
    from app.database.welding import WeldingSession
    from app.services.report_deadline import operation_deadline
    session = WeldingSession(SimpleNamespace())
    with operation_deadline(-1):
        with pytest.raises(WeldingUnavailable, match='超时'):
            session.fetch('SELECT 1')


def test_welding_query_timeout_defaults_match_cutting_and_keep_explicit_override():
    from app.config import _environment
    token = _environment.set({})
    try:
        cfg = Settings()
        assert cfg.weld_db_query_timeout == cfg.sqlserver_query_timeout == 120
        assert cfg.weld_db_connect_timeout == cfg.sqlserver_connect_timeout == 5
        _environment.set({'WELD_DB_QUERY_TIMEOUT_SECONDS': '30'})
        assert Settings().weld_db_query_timeout == 30
    finally:
        _environment.reset(token)


def test_each_welding_query_gets_full_timeout_unless_outer_budget_is_shorter(monkeypatch):
    from app.database import welding
    clock = [0]
    timeouts = []
    class Cursor:
        def execute(self, *args):
            clock[0] += 90
        def fetchmany(self, limit):
            return [{'healthy': 1}]
        def close(self):
            pass
    def cursor(**kwargs):
        timeouts.append((kwargs['read_timeout'], kwargs['write_timeout']))
        return Cursor()
    monkeypatch.setattr(welding, 'monotonic', lambda: clock[0])
    session = welding.WeldingSession(SimpleNamespace(cursor=cursor))
    assert session.fetch('SELECT 1') == [{'healthy': 1}]
    assert session.fetch('SELECT 1') == [{'healthy': 1}]
    assert timeouts == [(120, 120), (120, 120)]
    monkeypatch.setattr(welding, 'remaining_seconds', lambda: 9.2)
    session.fetch('SELECT 1')
    assert timeouts[-1] == (10, 10)


def test_cursor_cleanup_does_not_mask_cancelled_query(monkeypatch, caplog):
    from app.database.welding import WeldingSession
    from app.services.request_cancellation import SqlRequestCancelled, sql_request_cancellations
    class Cursor:
        def execute(self, *args):
            pass
        def fetchmany(self, limit):
            return []
        def close(self):
            raise RuntimeError('private-cleanup-secret')
    checks = iter([False, True])
    monkeypatch.setattr(sql_request_cancellations, 'is_cancelled', lambda _: next(checks))
    session = WeldingSession(SimpleNamespace(cursor=lambda **kwargs: Cursor()))
    with pytest.raises(SqlRequestCancelled):
        session.fetch('SELECT 1')
    assert 'private-cleanup-secret' not in caplog.text


def test_failed_rollback_still_closes_and_discards_pool():
    from app.services.request_cancellation import SqlRequestCancelled
    calls = []
    class Connection:
        def start_transaction(self, **kwargs):
            pass
        def rollback(self):
            calls.append('rollback')
            raise RuntimeError('broken protocol')
        def close(self):
            calls.append('close')
    client = WeldingClient(Settings())
    client._pool = SimpleNamespace(get_connection=Connection)
    client._get_pool = lambda: client._pool
    with pytest.raises(SqlRequestCancelled):
        with client.snapshot():
            raise SqlRequestCancelled('cancelled')
    assert calls == ['rollback', 'close']
    assert client._pool is None


def test_failed_pool_checkout_does_not_poison_later_requests():
    def broken():
        raise RuntimeError('broken protocol')
    client = WeldingClient(Settings())
    client._pool = SimpleNamespace(get_connection=broken)
    client._get_pool = lambda: client._pool
    with pytest.raises(WeldingUnavailable):
        with client.snapshot():
            pytest.fail('broken pool must not yield')
    assert client._pool is None


def test_aggregate_validates_time_once_per_source_row():
    from app.database.welding import job_source
    source, values = job_source(query(device_no=0))
    assert 'j.device_no = %s' in source and values[-1] == 0
    assert 'AS valid_end_time' in source and 'LIMIT 18446744073709551615' in source
    aggregate = job_aggregate()
    assert 'REGEXP' not in aggregate and 'j.valid_end_time' in aggregate


def test_readonly_repeatable_snapshot_rollback_and_secret_redaction(caplog):
    calls = []
    class Connection:
        def start_transaction(self, **kwargs):
            calls.append(kwargs)
        def rollback(self):
            calls.append('rollback')
        def close(self):
            calls.append('close')
    client = WeldingClient(Settings())
    client._get_pool = lambda: SimpleNamespace(get_connection=Connection)
    with pytest.raises(WeldingUnavailable):
        with client.snapshot():
            raise RuntimeError('password=secret@10.0.0.1')
    assert calls == [{'isolation_level': 'REPEATABLE READ', 'consistent_snapshot': True, 'readonly': True}, 'rollback', 'close']
    assert 'secret' not in caplog.text


@pytest.mark.parametrize('code,message', [(1045, '认证失败'), (1142, '权限不足'), (1054, '表结构')])
def test_source_failures_explain_safe_error_category(code, message):
    class DriverError(RuntimeError):
        errno = code
    def unavailable():
        raise DriverError('private-password@private-host')
    client = WeldingClient(Settings())
    client._get_pool = unavailable
    with pytest.raises(WeldingUnavailable, match=message) as error:
        with client.snapshot():
            pass
    assert 'private' not in str(error.value)


def test_ambiguous_part_seams_do_not_return_guessed_records():
    class Session:
        def fetch(self, sql, values, **kwargs):
            assert sql.startswith('SELECT COUNT(*)') and values == ('duplicate',)
            return [{'total': 2}]
    repository = WeldingRepository(None)
    assert repository.seams(Session(), {'serial_number': 'duplicate'}, 1, 20) == ([], 0, True)
    assert repository.seams(Session(), {'serial_number': 'Unknown'}, 1, 20) == ([], 0, True)


def test_exact_serial_association_does_not_require_unverified_robot_mapping():
    calls = []
    class Session:
        def fetch(self, sql, values, **kwargs):
            calls.append((sql, values))
            return [{'id': 2, 'robot_no': 2}] if 'LIMIT' in sql else [{'total': 1}]
    rows, total, ambiguous = WeldingRepository(None).seams(Session(), {'serial_number': 'full-serial'}, 2, 20)
    assert not ambiguous and total == 1 and rows[0]['robot_no'] == 2
    assert calls[-1][1] == ('full-serial', 20, 20)
    assert 'robot_no =' not in calls[-1][0]


def report_data():
    return {'summary': {'weld_length_m': 2, 'involved_part_count': 1, 'working_hours': 1},
            'items': [{'id': 1, 'serial_number': '<script>alert(1)</script>', 'weld_length_m': 2}], 'total': 1,
            'daily': [{'production_date': '2026-10-01', 'weld_length_m': 2}],
            'query': query().model_dump(mode='json'), 'meta': {'calculated_at': '2026-10-01T12:00:00+08:00',
            'timezone': 'Asia/Shanghai', 'data_updated_at': {}, 'notices': [], 'quality': {}, 'unlinked_part_count': 0}}


def test_analysis_version_ignores_paging_but_tracks_filters_and_association():
    from copy import deepcopy
    data = {**report_data(), 'equipment': []}
    version = WeldingService.version(data)
    reordered = deepcopy(data)
    reordered['query'].update(page=2, sort_by='length', descending=False)
    assert WeldingService.version(reordered) == version
    reordered['query']['device_no'] = 0
    assert WeldingService.version(reordered) != version
    changed = deepcopy(data)
    changed['meta']['ambiguous_part_count'] = 1
    assert WeldingService.version(changed) != version


def test_exports_cover_all_filters_escape_text_and_enforce_limits(tmp_path):
    data = report_data()
    class Service:
        def report(self, kind, selected, *, export_limit):
            assert kind == 'jobs' and export_limit in (5001, 100001)
            return data
    class Pdf:
        def render(self, path):
            assert '&lt;script&gt;' in path.read_text()
            result = path.with_suffix('.pdf')
            result.write_bytes(b'%PDF-test')
            return result
    export = WeldingExport(Settings(project_root=tmp_path), Service(), Pdf())
    csv, _ = export.generate('jobs', query(), 'csv')
    assert csv.startswith(b'\xef\xbb\xbf') and '工件流水号' in csv.decode('utf-8-sig')
    html, _ = export.generate('jobs', query(), 'html')
    assert b'&lt;script&gt;' in html and b'<script>alert' not in html
    pdf, media = export.generate('jobs', query(), 'pdf')
    assert pdf == b'%PDF-test' and media == 'application/pdf'
    data['total'] = 5001
    with pytest.raises(ValueError, match='超过5,000'):
        export.generate('jobs', query(), 'html')
    escaped = csv_bytes([('text', '内容'), ('number', '数值')], [{'text': ' =HYPERLINK("bad")', 'number': -2}], [])
    assert "' =HYPERLINK" in escaped.decode() and ',-2' in escaped.decode()


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(router)
    app.include_router(lines_router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    app.state.welding = SimpleNamespace(settings=Settings(weld_enabled=True, llm_enabled=False))
    return TestClient(app)


def test_api_validation_disabled_missing_and_capabilities(api):
    for suffix in ['', '&page_size=101', '&page=1&page=2', '&sort_by=DROP', '&bad=1']:
        url = '/api/v1/welding/jobs' + ('?start=2026-10-01&end=2026-10-02' + suffix if suffix else '')
        assert api.get(url).status_code == 422
    items = api.get('/api/v1/production-lines').json()['data']['items']
    weld = next(item for item in items if item['id'] == 'welding')
    assert weld['type'] == 'welding' and weld['enabled'] is True
    assert 'alarms' not in weld['capabilities'] and 'dispatch' not in weld['capabilities']
    api.app.state.welding.settings = Settings(weld_enabled=False)
    assert api.get('/api/v1/welding/health').status_code == 503
    disabled = next(item for item in api.get('/api/v1/production-lines').json()['data']['items'] if item['id'] == 'welding')
    assert disabled['enabled'] is False
    assert api.post('/api/v1/welding/dispatch').status_code == 404


def test_worker_cannot_read_welding_or_line_catalog(api):
    api.app.dependency_overrides.pop(require_admin)
    from app.api.auth import require_user
    api.app.dependency_overrides[require_user] = lambda: {'role': 'worker'}
    assert api.get('/api/v1/welding/health').status_code == 403
    assert api.get('/api/v1/production-lines').status_code == 403


def test_ai_input_version_and_unavailable_model(api):
    instance = api.app.state.welding
    instance.report = lambda *args, **kwargs: {'data_version': 'b' * 64}
    body = {'query': query().model_dump(mode='json'), 'data_version': 'a' * 64}
    assert api.post('/api/v1/welding/analysis', json=body).status_code == 409
    body['data_version'] = 'b' * 64
    assert api.post('/api/v1/welding/analysis', json=body).status_code == 503
    prompt = analysis_system_prompt('welding')
    assert '毫米' not in prompt and '长度单位米' in prompt and '派发' in prompt
    assert analysis_system_prompt('equipment').startswith('你是切割生产报表')


def test_ai_uses_only_aggregate_context_and_reuses_verified_version(api, monkeypatch):
    from app.models.domain import AnalysisResult
    instance = WeldingService(Settings(weld_enabled=True, llm_enabled=True), None)
    data = report_data()
    data.update(data_version='a' * 64, equipment=[])
    instance.report = lambda *args, **kwargs: data
    api.app.state.welding = instance
    calls = []
    def analyze(self, kind, context):
        calls.append(context)
        assert kind == 'welding' and '<script>' not in str(context)
        return AnalysisResult(status='success', overview='概况', features='特点', alerts='数据问题', suggestions='核查')
    monkeypatch.setattr('app.services.llm_service.LlmService.analyze_context', analyze)
    body = {'query': query().model_dump(mode='json'), 'data_version': 'a' * 64}
    assert api.post('/api/v1/welding/analysis', json=body).status_code == 200
    assert api.post('/api/v1/welding/analysis', json=body).json()['data']['cached'] is True
    assert len(calls) == 1


def test_http_context_blocks_cutting_operations_and_validation_never_echoes_password():
    from app.main import app
    previous = app.dependency_overrides.get(require_admin)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    try:
        client = TestClient(app)
        denied = client.post('/api/v1/cutting/dispatch', headers={'X-Production-Line': 'welding'})
        assert denied.status_code == 403
        secret = 'private-validation-secret' * 20
        result = client.put('/api/v1/database/welding/configuration', json={'password': secret})
        assert result.status_code == 422 and secret not in result.text
    finally:
        if previous is None:
            app.dependency_overrides.pop(require_admin)
        else:
            app.dependency_overrides[require_admin] = previous


def test_welding_hot_configuration_validation_and_password_retention():
    from app.api.database import WeldingConnectionRequest, _welding_env_values
    initial = Settings(weld_db_password='saved-secret', data_source_mode='demo', worker_database_auth_enabled=False, file_database_enabled=False)
    payload = WeldingConnectionRequest(enabled=True, host='db.test', username='readonly')
    values = _welding_env_values(payload, initial)
    updated = parse_database_settings(initial, values)
    assert updated.weld_enabled and updated.weld_db_password == 'saved-secret'
    assert initial.weld_enabled is False
    with pytest.raises(ValueError):
        parse_database_settings(initial, {**values, 'WELD_DB_PORT': 0})
    with pytest.raises(ValueError):
        parse_database_settings(initial, {**values, 'WELD_DB_TIMEZONE': 'invalid'})
    with pytest.raises(ValueError):
        parse_database_settings(initial, {**values, 'WELD_DB_PASSWORD': ''})


def test_welding_diagnostics_report_missing_profile_without_returning_secrets():
    from app.services.configuration_diagnostics import validate_values, safe_value
    from app.api.database import WeldingConnectionRequest
    cfg = Settings(weld_enabled=True, weld_db_host='', weld_db_user='', weld_db_password='', weld_db_pool_size=0, weld_db_timezone='bad')
    invalid = validate_values(cfg, {})
    assert {'WELD_DB_HOST', 'WELD_DB_USER', 'WELD_DB_PASSWORD', 'WELD_DB_POOL_SIZE', 'WELD_DB_TIMEZONE'} <= invalid
    assert safe_value('private', 'WELD_DB_PASSWORD') == '已配置'
    with pytest.raises(ValidationError):
        WeldingConnectionRequest(timezone='bad')


def test_source_audit_keeps_core_results_when_optional_tables_are_not_authorized():
    import json
    from pathlib import Path
    import runpy
    from mysql.connector.errors import ProgrammingError
    class Session:
        def fetch(self, sql, *args, **kwargs):
            if 'production_stats' in sql or 'weld_plan_info_table' in sql:
                raise ProgrammingError(msg='private-driver-details', errno=1142)
            if 'SHOW GRANTS' in sql:
                return [{'grant': 'GRANT SELECT ON test.* TO reader'}]
            if sql.startswith('SHOW'):
                return []
            return [{'total': 0, 'updated_at': None}]
    class Client:
        @contextmanager
        def snapshot(self):
            yield Session()
    script = runpy.run_path(str(Path(__file__).resolve().parents[2] / 'scripts' / 'check_welding.py'))
    result = script['inspect_source'](Client())
    assert result['tables']['weld_info_table']['total'] == 0
    assert result['tables']['production_stats']['error_code'] == 1142
    assert 'private-driver-details' not in json.dumps(result)
