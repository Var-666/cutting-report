from tests.web_fixtures import embedded_app, database_services, replace_database_services
from tests.postgres_fixtures import state_settings
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.auth import require_admin
from app.modules.welding.routes import router
from app.api.production_lines import router as lines_router
from app.core.operations_logging import safe_record
from tests.line_fixtures import welding_settings, runtime_line
from app.device_config import load_line_catalog
from app.database.source_registry import DatabaseSourceRegistry
from app.core.sql_capacity import SqlCapacity
from app.database.mysql import MySqlClient, MySqlSession
from app.database.mysql import MySqlUnavailable
from app.modules.welding.repository import WeldingRepository, job_conditions, job_aggregate
from app.modules.welding.models import WeldingQuery
from app.runtime.database import parse_database_settings
from app.modules.reports.analysis.llm import analysis_system_prompt
from app.modules.welding.export import WeldingExport, csv_bytes
from app.modules.welding.service import WeldingService, mapped_row, summary_values


def mysql_client(cfg=None):
    cfg = cfg or welding_settings()
    source = DatabaseSourceRegistry.from_json(cfg.database_sources_json).get('welding-production')
    return MySqlClient(source.model_copy(update={'enabled': cfg.weld_enabled}), label='焊接', capacity=SqlCapacity(cfg))


def query(**kwargs):
    return WeldingQuery(start=date(2026, 10, 1), end=date(2026, 10, 31), **kwargs)


def test_closing_job_aggregation_keeps_midnight_and_17_buckets_separate(monkeypatch):
    import sqlite3

    stamps = ['2026-10-31 23:59:59', '2026-11-01 00:00:00', '2026-11-01 23:59:59',
              '2026-11-02 00:00:00', '2026-11-02 17:00:00', '2026-11-03 16:59:59', '2026-11-03 17:00:00']
    selected = WeldingQuery(start=date(2026, 11, 3), end=date(2026, 11, 3), device_no=0)
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.create_function('CONCAT', 2, lambda left, right: left + right)
        connection.execute('CREATE TABLE jobs (end_time TEXT, valid_end_time INT, weld_lenght INT, used_time INT)')
        connection.executemany('INSERT INTO jobs VALUES (?,1,1000,3600)', [(stamp,) for stamp in stamps])
        monkeypatch.setattr('app.modules.welding.repository.job_rows', lambda query, *, device_nos, bounds: (
            'SELECT * FROM jobs WHERE end_time >= ? AND end_time < ?', bounds))

        def fetch(sql, params, *, limit):
            # Execute the repository's aggregation; only adapt MySQL's LEFT spelling for SQLite.
            sql = sql.replace('LEFT(j.end_time, 10)', 'SUBSTR(j.end_time, 1, 10)')
            return [dict(row) for row in connection.execute(sql, params)]

        repository = WeldingRepository(None)
        service = WeldingService(welding_settings(), repository)
        now = datetime(2026, 11, 3, 18, tzinfo=ZoneInfo('Asia/Shanghai'))
        for kind in ['daily', 'jobs', 'equipment']:
            columns = service._closing_comparison(SimpleNamespace(fetch=fetch), kind, selected, now)
            assert [column['totals']['job_count'] for column in columns] == [2, 2, 4]
            assert [column['totals']['weld_length_m'] for column in columns] == [2, 2, 4]
            assert [column['totals']['working_hours'] for column in columns] == [2, 2, 4]
            assert columns[1]['start'].isoformat() == '2026-11-02T00:00:00+08:00'
            assert columns[2]['start'].isoformat() == '2026-11-01T00:00:00+08:00'
            assert columns[1]['end'] == columns[2]['end'] == datetime(2026, 11, 3, tzinfo=now.tzinfo)


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
    client = mysql_client(welding_settings(weld_enabled=False))
    assert client._pool is None
    with pytest.raises(MySqlUnavailable, match='尚未启用'):
        with client.snapshot():
            pytest.fail('disabled client must not yield')


def test_outer_query_budget_exhaustion_does_not_start_another_sql():
    from app.database.mysql import MySqlSession
    from app.core.report_deadline import operation_deadline
    session = MySqlSession(SimpleNamespace())
    with operation_deadline(-1):
        with pytest.raises(MySqlUnavailable, match='超时'):
            session.fetch('SELECT 1')


def test_mysql_query_timeout_defaults_and_explicit_source_override():
    config = DatabaseSourceRegistry.from_json(welding_settings().database_sources_json).get('welding-production')
    assert config.query_timeout == 120 and config.connect_timeout == 5
    assert MySqlClient(config.model_copy(update={'query_timeout': 30}), capacity=SqlCapacity(welding_settings())).query_timeout == 30


def test_each_welding_query_gets_full_timeout_unless_outer_budget_is_shorter(monkeypatch):
    from app.modules.welding import repository as welding
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
    monkeypatch.setattr('app.database.mysql.monotonic', lambda: clock[0])
    session = MySqlSession(SimpleNamespace(cursor=cursor))
    assert session.fetch('SELECT 1') == [{'healthy': 1}]
    assert session.fetch('SELECT 1') == [{'healthy': 1}]
    assert timeouts == [(120, 120), (120, 120)]
    monkeypatch.setattr('app.database.mysql.remaining_seconds', lambda: 9.2)
    session.fetch('SELECT 1')
    assert timeouts[-1] == (10, 10)


def test_cursor_cleanup_does_not_mask_cancelled_query(monkeypatch, caplog):
    from app.database.mysql import MySqlSession
    from app.core.request_cancellation import SqlRequestCancelled, sql_request_cancellations
    class Cursor:
        def execute(self, *args):
            pass
        def fetchmany(self, limit):
            return []
        def close(self):
            raise RuntimeError('private-cleanup-secret')
    checks = iter([False, True])
    monkeypatch.setattr(sql_request_cancellations, 'is_cancelled', lambda _: next(checks))
    session = MySqlSession(SimpleNamespace(cursor=lambda **kwargs: Cursor()))
    with pytest.raises(SqlRequestCancelled):
        session.fetch('SELECT 1')
    assert 'private-cleanup-secret' not in caplog.text


def test_failed_rollback_still_closes_and_discards_pool():
    from app.core.request_cancellation import SqlRequestCancelled
    calls = []
    class Connection:
        def start_transaction(self, **kwargs):
            pass
        def rollback(self):
            calls.append('rollback')
            raise RuntimeError('broken protocol')
        def close(self):
            calls.append('close')
    client = mysql_client(welding_settings())
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
    client = mysql_client(welding_settings())
    client._pool = SimpleNamespace(get_connection=broken)
    client._get_pool = lambda: client._pool
    with pytest.raises(MySqlUnavailable):
        with client.snapshot():
            pytest.fail('broken pool must not yield')
    assert client._pool is None


def test_aggregate_validates_time_once_per_source_row():
    from app.modules.welding.repository import job_source
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
    client = mysql_client(welding_settings())
    client._get_pool = lambda: SimpleNamespace(get_connection=Connection)
    with pytest.raises(MySqlUnavailable):
        with client.snapshot():
            raise RuntimeError('password=secret@10.0.0.1')
    assert calls == [{'isolation_level': 'REPEATABLE READ', 'consistent_snapshot': True, 'readonly': True}, 'rollback', 'close']
    records = [safe_record(record, 'web', 'test') for record in caplog.records]
    assert any('exception' in record for record in records)
    assert 'secret@10.0.0.1' not in repr(records)


@pytest.mark.parametrize('code,message', [(1045, '认证失败'), (1142, '权限不足'), (1054, '表结构')])
def test_source_failures_explain_safe_error_category(code, message):
    class DriverError(RuntimeError):
        errno = code
    def unavailable():
        raise DriverError('private-password@private-host')
    client = mysql_client(welding_settings())
    client._get_pool = unavailable
    with pytest.raises(MySqlUnavailable, match=message) as error:
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


def test_export_reuses_matching_persisted_success_after_restart(state_database, tmp_path, monkeypatch):
    from app.modules.reports.repository import MetadataRepository
    from app.models.domain import AnalysisResult, to_primitive
    cfg = welding_settings(project_root=tmp_path, llm_enabled=True)
    def build():
        return WeldingService(cfg, None, analyses=MetadataRepository(state_database, device_id=cfg.device_id, analysis_scope='welding:export'))
    monkeypatch.setattr('app.modules.welding.service.LlmService.analyze_context', lambda *_: AnalysisResult(
        status='success', overview='原有焊接分析', features='原有特征', alerts='原有关注', suggestions='核对记录',
        facts=[{'label': '原有作业次数', 'value': 3}]))
    original = {**report_data(), 'equipment': [], 'data_version': 'a' * 64}
    first = to_primitive(build().analyze_report('jobs', query(), original, 'a' * 64))
    restarted = build()
    current = {**report_data(), 'equipment': [], 'data_version': 'b' * 64}
    current['summary']['job_count'] = 987654321
    monkeypatch.setattr('app.modules.welding.service.LlmService.analyze_context',
                        lambda *_: (_ for _ in ()).throw(TimeoutError('fixture model outage')))
    exporter = WeldingExport(cfg, restarted, None)
    restarted.report = lambda *_args, **_kwargs: original
    content, _ = exporter.generate('jobs', query(), 'html')
    html = content.decode()
    assert '原有焊接分析' in html
    assert first['generated_at'] in html and '已缓存' in html
    restarted.report = lambda *_args, **_kwargs: current
    content, _ = exporter.generate('jobs', query(), 'html')
    html = content.decode()
    assert '987654321' in html and '历史分析与本次导出统计不匹配' in html
    assert '原有焊接分析' not in html


def test_exports_cover_all_filters_escape_text_and_enforce_limits(tmp_path):
    data = report_data()
    data['data_version'] = 'matching'
    data['summary']['job_count'] = 1
    data['items'].append({'id': 2, 'end_time': '2026-10-01 25:00:00'})
    data['total'] = 2
    data['meta']['quality']['invalid_time_rows'] = 1
    class Service:
        def report(self, kind, selected, *, export_limit, include_closing_comparison):
            assert kind == 'jobs' and export_limit in (5001, 100001)
            assert include_closing_comparison is (export_limit == 5001)
            return data
        def analyze_report(self, kind, selected, report, version):
            if version == 'matching':
                return {'mode': 'model', 'status': 'success', 'data_version': version, 'overview': '焊接产出简报',
                        'features': '工艺分布', 'alerts': '数据核查', 'suggestions': '核对记录'}
    class Pdf:
        def render(self, path):
            assert '&lt;script&gt;' in path.read_text()
            assert '焊接产出简报' in path.read_text()
            result = path.with_suffix('.pdf')
            result.write_bytes(b'%PDF-test')
            return result
    export = WeldingExport(welding_settings(project_root=tmp_path), Service(), Pdf())
    csv, _ = export.generate('jobs', query(), 'csv')
    assert csv.startswith(b'\xef\xbb\xbf') and '工件流水号' in csv.decode('utf-8-sig')
    html, _ = export.generate('jobs', query(), 'html')
    assert b'&lt;script&gt;' in html and b'<script>alert' not in html
    assert '焊接产出简报' in html.decode()
    assert '焊接作业明细（2行）' in html.decode()
    assert '<strong>1</strong><small>次</small>' in html.decode()
    assert '时间无效 1' in html.decode()
    assert '这些记录不计入本期作业次数' not in html.decode()
    pdf, media = export.generate('jobs', query(), 'pdf')
    assert pdf == b'%PDF-test' and media == 'application/pdf'
    data['data_version'] = 'changed'
    html, _ = export.generate('jobs', query(), 'html')
    assert '焊接产出简报' not in html.decode()
    data['total'] = 5001
    with pytest.raises(ValueError, match='超过5,000'):
        export.generate('jobs', query(), 'html')
    escaped = csv_bytes([('text', '内容'), ('number', '数值')], [{'text': ' =HYPERLINK("bad")', 'number': -2}], [])
    assert "' =HYPERLINK" in escaped.decode() and ',-2' in escaped.decode()


@pytest.fixture
def api(state_database):
    app = embedded_app()
    app.state.runtime.database = state_database
    app.include_router(router)
    app.include_router(lines_router)
    app.dependency_overrides[require_admin] = lambda: {'role': 'admin'}
    from app.modules.reports.repository import MetadataRepository
    instance = WeldingService(welding_settings(llm_enabled=False), None,
        analyses=MetadataRepository(state_database, device_id='welding', analysis_scope='welding:test'))
    replace_database_services(app, lines={'welding': runtime_line(instance, 'welding')})
    replace_database_services(app, catalog=load_line_catalog(instance.settings.production_lines_json))
    return TestClient(app, headers={'X-Production-Line': 'welding'})


def test_api_validation_disabled_missing_and_capabilities(api):
    for suffix in ['', '&page_size=101', '&page=1&page=2', '&sort_by=DROP', '&bad=1']:
        url = '/api/v1/welding/jobs' + ('?start=2026-10-01&end=2026-10-02' + suffix if suffix else '')
        assert api.get(url).status_code == 422
    items = api.get('/api/v1/production-lines').json()['data']['items']
    weld = next(item for item in items if item['id'] == 'welding')
    assert weld['type'] == 'welding' and weld['enabled'] is True
    assert 'alarms' not in weld['capabilities'] and 'dispatch' not in weld['capabilities']
    replace_database_services(api.app, catalog=database_services(api.app).catalog.model_copy(update={'lines': tuple(line.model_copy(update={'enabled': False}) if line.id == 'welding' else line for line in database_services(api.app).catalog.lines)}))
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
    instance = database_services(api.app).lines['welding'].service
    instance.page = lambda *args, **kwargs: {'data_version': 'b' * 64}
    body = {'query': query().model_dump(mode='json'), 'data_version': 'a' * 64}
    assert api.post('/api/v1/welding/analysis', json=body).status_code == 409
    body['data_version'] = 'b' * 64
    assert api.post('/api/v1/welding/analysis', json=body).status_code == 503
    prompt = analysis_system_prompt('welding')
    assert '毫米' not in prompt and '长度单位米' in prompt and '派发' in prompt
    assert analysis_system_prompt('equipment') != prompt and '切割生产' in analysis_system_prompt('equipment')


def test_ai_uses_only_aggregate_context_and_reuses_verified_version(api, monkeypatch):
    from app.models.domain import AnalysisResult
    from app.modules.reports.repository import MetadataRepository
    instance = WeldingService(welding_settings(weld_enabled=True, llm_enabled=True), None,
        analyses=MetadataRepository(api.app.state.runtime.database, device_id='welding', analysis_scope='welding:test'))
    data = report_data()
    data.update(data_version='a' * 64, equipment=[])
    instance.report = lambda *args, **kwargs: data
    database_services(api.app).lines['welding'] = runtime_line(instance, 'welding')
    calls = []
    def analyze(self, kind, context):
        calls.append(context)
        assert kind == 'welding' and '<script>' not in str(context)
        return AnalysisResult(status='success', overview='概况', features='特点', alerts='数据问题', suggestions='核查')
    monkeypatch.setattr('app.modules.reports.analysis.llm.LlmService.analyze_context', analyze)
    body = {'query': query().model_dump(mode='json'), 'data_version': 'a' * 64}
    assert api.post('/api/v1/welding/analysis', json=body).status_code == 200
    assert api.post('/api/v1/welding/analysis', json=body).json()['data']['cached'] is True
    assert len(calls) == 1


def test_http_context_blocks_cutting_operations_and_validation_never_echoes_password(postgres_state, tmp_path, monkeypatch):
    from app.main import app
    from app.runtime.application import build_runtime
    from tests.line_fixtures import configuration
    monkeypatch.setattr(app.state, 'runtime', build_runtime(state_settings(configuration(tmp_path, welding=True),postgres_state), role='web'), raising=False)
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


def test_welding_connection_boundary_validates_timezone_without_echoing_secrets():
    from app.services.database_configuration import WeldingConnectionRequest
    with pytest.raises(ValidationError):
        WeldingConnectionRequest(timezone='invalid')
    from app.services.configuration_diagnostics import safe_value
    assert safe_value('private', 'DATABASE_SOURCES_JSON') == '已配置'


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


def test_connection_pool_default_matches_execution_capacity_and_remains_readonly(monkeypatch):
    import mysql.connector.pooling
    from contextvars import ContextVar
    from app import config
    from app.api.workloads import build_workloads
    monkeypatch.setattr(config, '_environment', ContextVar('test-welding-config', default={}))
    captured = {}
    class Connection:
        def start_transaction(self, **values):
            captured['transaction'] = values
        def rollback(self):
            captured['rollback'] = True
        def close(self):
            captured['closed'] = True
    def pool(**values):
        captured.update(values)
        return SimpleNamespace(get_connection=Connection)
    monkeypatch.setattr(mysql.connector.pooling, 'MySQLConnectionPool', pool)
    cfg = welding_settings()
    query_pool = build_workloads(cfg, SqlCapacity(cfg))['query']
    assert query_pool._threads.total_tokens == cfg.http_query_max_concurrent
    assert query_pool.max_waiters == cfg.http_max_waiters and query_pool.queue_timeout_seconds == cfg.sql_queue_timeout_seconds
    with mysql_client(cfg).snapshot() as session:
        assert session.timeout == 120
    assert captured['pool_size'] == 4 and captured['use_pure']
    assert captured['transaction'] == {'isolation_level': 'REPEATABLE READ', 'consistent_snapshot': True, 'readonly': True}
    assert captured['rollback'] and captured['closed']


@pytest.mark.parametrize('name,code,category', [
    ('PoolError', None, 'connection_pool_exhausted'),
    ('ReadTimeoutError', None, 'query_timeout'),
    ('DriverError', 3024, 'query_timeout'),
    ('DriverError', 1064, 'sql_error'),
    ('DriverError', 2003, 'connection_failed'),
    ('DriverError', 1054, 'schema_mismatch'),
])
def test_source_capacity_timeout_sql_and_connection_errors_remain_distinct(name, code, category, caplog):
    error = type(name, (RuntimeError,), {'errno': code})('private-credentials-and-SQL')
    def broken():
        raise error
    client = mysql_client(welding_settings())
    pool = SimpleNamespace(get_connection=broken)
    client._pool = pool
    client._get_pool = lambda: pool
    with pytest.raises(MySqlUnavailable) as caught:
        with client.snapshot():
            pytest.fail('broken source must not yield')
    assert caught.value.category == category
    records = [safe_record(record, 'web', 'test') for record in caplog.records]
    assert any(record.get('exception', {}).get('type') == name for record in records)
    assert 'private' not in str(caught.value) + repr(records)
    assert (client._pool is pool) == (category == 'connection_pool_exhausted')


def test_jobs_reuse_snapshot_source_count_including_invalid_time_candidates():
    calls = []
    class Session:
        def fetch(self, sql, values=(), **kwargs):
            calls.append((sql, values))
            assert not sql.startswith('SELECT COUNT')
            return [{'id': 2}]
    rows, total = WeldingRepository(None).jobs(Session(), query(page=2, page_size=1), total=3)
    assert total == 3 and rows == [{'id': 2}] and len(calls) == 1
    assert calls[0][1][-2:] == (1, 1)
    assert WeldingRepository(None).jobs(Session(), query(), total=3, limit=3) == ([], 3)
    assert len(calls) == 1


def test_report_passes_the_same_snapshot_count_to_paginated_jobs():
    class Session:
        def fetch(self, sql, *args, **kwargs):
            return [{'unlinked': 0, 'ambiguous': 0}] if 'AS unlinked' in sql else []
    class Client:
        @contextmanager
        def snapshot(self):
            yield Session()
    class Repository:
        device_nos = ()
        conditions = staticmethod(job_conditions)
        speed_distribution = WeldingRepository.speed_distribution
        client = Client()
        def summary(self, session, selected):
            return {'source_rows': 3, 'job_count': 2, **dict.fromkeys([
                'invalid_time_rows', 'invalid_serial_rows', 'invalid_length_rows',
                'invalid_duration_rows', 'unverified_state_rows'], 0)}
        def daily(self, *args):
            return []
        def equipment(self, *args):
            return []
        def statistics(self, session, selected):
            return self.summary(session, selected), [], [], [], {'unlinked': 0, 'ambiguous': 0}
        def source_info(self, *args):
            return {'devices': [], 'updated_at': {}}
        def jobs(self, session, selected, *, limit, total):
            assert total == 3 and selected.page == 2 and limit is None
            return [{'id': 2, 'serial_number': 'part'}], total
    report = WeldingService(welding_settings(), Repository()).report('jobs', query(page=2, page_size=1))
    assert report['total'] == 3 and report['summary']['job_count'] == 2
    assert report['items'][0]['id'] == 2 and report['data_version']


def test_oversized_welding_groups_preserve_daily_totals_and_limit_equipment_exports(monkeypatch):
    class Session:
        def fetch(self, sql, *args, **kwargs):
            if 'GROUP BY device_no' in sql:
                return [{'device_no': number} for number in range(1001)]
            assert 'MAX(update_date)' in sql
            return [{'updated_at': None}]

    @contextmanager
    def snapshot():
        yield Session()

    repository = WeldingRepository(SimpleNamespace(snapshot=snapshot))
    summary = dict.fromkeys(('invalid_time_rows', 'invalid_serial_rows', 'invalid_length_rows',
                             'invalid_duration_rows', 'unverified_state_rows'), 0)
    summary.update(job_count=10001)
    daily = [{'production_date': '2026-10-31', 'job_count': 10001}]
    equipment = [{'device_no': 0, 'robot_no': number, 'job_count': 1} for number in range(10001)]
    devices = [{'device_no': number, 'job_count': 1} for number in range(1001)]
    monkeypatch.setattr(repository, 'statistics', lambda *_: (summary, daily, equipment, devices, {'unlinked': 0, 'ambiguous': 0}))
    service = WeldingService(welding_settings(), repository)

    report = service.report('daily', query())
    assert report['summary']['job_count'] == report['items'][0]['job_count'] == 10001
    assert len(report['meta']['devices']) == len(report['device_distribution']) == 1000
    assert {'device_directory', 'device_distribution', 'equipment_groups'} <= report['meta']['pending_metrics'].keys()
    assert service.report('equipment', query())['total'] == 10000
    with pytest.raises(ValueError, match='缩小筛选范围后导出'):
        service.report('equipment', query(), export_limit=100000)


def test_http_source_error_keeps_safe_category_without_retry_header(api):
    def failed(*args, **kwargs):
        raise MySqlUnavailable('焊接SQL执行失败', category='sql_error')
    database_services(api.app).lines['welding'].service.page = failed
    result = api.get('/api/v1/welding/jobs?start=2026-10-01&end=2026-10-02')
    assert result.status_code == 503
    assert result.headers['X-Welding-Error'] == 'sql_error'
    assert 'Retry-After' not in result.headers


def test_snapshot_preparation_has_a_distinct_reason_from_queue_or_database_failures(api):
    from app.modules.reports.snapshots import SnapshotRefreshBusy
    def preparing(*args, **kwargs):
        raise SnapshotRefreshBusy('统计正在生成，请稍后重试')
    database_services(api.app).lines['welding'].service.page = preparing
    result = api.get('/api/v1/welding/jobs?start=2026-10-01&end=2026-10-02')
    assert result.status_code == 503
    assert result.headers['X-Welding-Error'] == 'snapshot_refresh_busy'
    assert result.headers['Retry-After'] == '5'


def test_snapshot_queue_rejection_is_not_reported_as_preparation(api):
    from app.modules.reports.snapshots import SnapshotRefreshRejected
    def rejected(*args, **kwargs):
        raise SnapshotRefreshRejected('后台刷新队列已满')
    database_services(api.app).lines['welding'].service.page = rejected
    result = api.get('/api/v1/welding/jobs?start=2026-10-01&end=2026-10-02')
    assert result.status_code == 503
    assert result.headers['X-Welding-Error'] == 'snapshot_queue_full'
    assert 'Retry-After' not in result.headers


@pytest.mark.parametrize('stage', ['pdf', 'html', 'analysis'])
def test_pdf_and_ai_release_welding_capacity_before_slow_processing(api, monkeypatch, tmp_path, stage):
    import asyncio
    import anyio
    import httpx
    from threading import BoundedSemaphore, Event
    from app.modules.welding import routes as welding
    from app.api.workloads import RequestWorkload
    from app.models.domain import AnalysisResult
    from app.core.sql_capacity import SqlCapacity
    entered, release = Event(), Event()
    pool = api.app.state.workloads['query']
    monkeypatch.setattr(pool, '_slots', BoundedSemaphore(1))
    monkeypatch.setattr(pool, '_threads', anyio.CapacityLimiter(1))
    monkeypatch.setattr(pool, 'max_waiters', 2)
    monkeypatch.setattr(pool, 'queue_timeout_seconds', 0.1)
    api.app.state.workloads['pdf'] = RequestWorkload(1, 'PDF')
    analysis_pool = api.app.state.workloads['analysis'] = RequestWorkload(1, '分析')
    data = {**report_data(), 'equipment': [], 'data_version': 'a' * 64}
    instance = WeldingService(welding_settings(weld_enabled=True, llm_enabled=True), None)
    resource = SqlCapacity(replace(instance.settings, project_root=tmp_path))
    client = mysql_client(instance.settings)
    client.capacity = resource
    class Connection:
        def start_transaction(self, **options):
            assert options['readonly'] and options['isolation_level'] == 'REPEATABLE READ'
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(client, '_get_pool', lambda: SimpleNamespace(get_connection=Connection))
    source = {'open': False}
    def report(*args, **kwargs):
        source['open'] = True
        try:
            with client.snapshot():
                assert resource.status()['items'][0]['executing'] == 1
                return data
        finally:
            source['open'] = False
    instance.report = report
    database_services(api.app).lines['welding'] = runtime_line(instance, 'welding')
    def slow():
        assert not source['open']
        assert all(row['executing'] == row['waiting'] == 0 for row in resource.status()['items'])
        entered.set()
        assert release.wait(5)
    class Pdf:
        def render(self, path):
            slow()
            result = path.with_suffix('.pdf')
            result.write_bytes(b'%PDF-test')
            return result
    api.app.state.runtime.pdf_service = Pdf()
    def model(*args):
        slow()
        return AnalysisResult(status='success', overview='ok', features='ok', alerts='ok', suggestions='ok')
    monkeypatch.setattr('app.modules.welding.service.LlmService.analyze_context', model)

    async def exercise():
        async with httpx.AsyncClient(headers={'X-Production-Line': 'welding'}, transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
            if stage in ('pdf', 'html'):
                operation = client.get(f'/api/v1/welding/export?report_type=jobs&format={stage}&start=2026-10-01&end=2026-10-31')
            else:
                operation = client.post('/api/v1/welding/analysis', json={'query': query().model_dump(mode='json'), 'report_type': 'jobs', 'data_version': 'a' * 64})
            task = asyncio.create_task(operation)
            try:
                with anyio.fail_after(2):
                    while not entered.is_set():
                        await anyio.sleep(0.001)
                assert pool._slots.acquire(blocking=False)
                pool._slots.release()
                assert not analysis_pool._slots.acquire(blocking=False)
                with anyio.fail_after(1):
                    response = await client.get('/api/v1/welding/jobs?start=2026-10-01&end=2026-10-31')
                assert response.status_code == 200 and response.json()['data']['data_version'] == 'a' * 64
            finally:
                release.set()
                result = await task
            assert result.status_code == 200
            if stage == 'pdf':
                assert result.content == b'%PDF-test'
            elif stage == 'html':
                assert result.headers['content-type'].startswith('text/html')
            else:
                assert result.json()['data']['overview'] == 'ok'
            assert analysis_pool._slots.acquire(blocking=False)
            analysis_pool._slots.release()
    anyio.run(exercise)


@pytest.mark.parametrize('has_snapshot_storage', [False, True])
def test_four_http_queries_execute_and_fifth_waits_for_a_slot(api, monkeypatch, has_snapshot_storage):
    import asyncio
    import anyio
    import httpx
    from threading import BoundedSemaphore, Event, Lock
    from app.modules.welding import routes as welding
    pool = api.app.state.workloads['query']
    monkeypatch.setattr(pool, '_slots', BoundedSemaphore(4))
    monkeypatch.setattr(pool, '_threads', anyio.CapacityLimiter(4))
    monkeypatch.setattr(pool, 'max_waiters', 12)
    monkeypatch.setattr(pool, 'queue_timeout_seconds', 5)
    release, lock = Event(), Lock()
    counts = {'running': 0, 'peak': 0}
    if has_snapshot_storage:
        database_services(api.app).lines['welding'].service.snapshot_service = SimpleNamespace(coordination=None)
    def report(*args):
        with lock:
            counts['running'] += 1
            counts['peak'] = max(counts['peak'], counts['running'])
        try:
            assert release.wait(5)
            return {'data_version': 'ok'}
        finally:
            with lock:
                counts['running'] -= 1
    database_services(api.app).lines['welding'].service.page = report
    async def exercise():
        async with httpx.AsyncClient(headers={'X-Production-Line': 'welding'}, transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
            tasks = [asyncio.create_task(client.get('/api/v1/welding/jobs?start=2026-10-01&end=2026-10-02')) for _ in range(5)]
            try:
                with anyio.fail_after(2):
                    while counts['running'] != 4 or len(pool._queue) != 1:
                        await anyio.sleep(0.001)
                assert not any(task.done() for task in tasks)
            finally:
                release.set()
                responses = await asyncio.gather(*tasks)
            assert all(response.status_code == 200 for response in responses)
            assert counts['peak'] == 4 and not pool._queue
    anyio.run(exercise)


def test_ready_snapshots_do_not_wait_for_busy_source_connections(api, monkeypatch):
    import asyncio
    import anyio
    import httpx
    from threading import Event, Lock
    release, lock = Event(), Lock()
    counts = {'running': 0}

    def part(*args):
        with lock:
            counts['running'] += 1
        try:
            assert release.wait(5)
            return {'part': {'id': 1}, 'items': []}
        finally:
            with lock:
                counts['running'] -= 1

    database_services(api.app).lines['welding'].service.snapshot_service = SimpleNamespace(coordination=object())
    database_services(api.app).lines['welding'].service.part = part
    database_services(api.app).lines['welding'].service.page = lambda *_: {'data_version': 'ready', 'meta': {'report_data': {'source': 'snapshot'}}}

    async def exercise():
        async with httpx.AsyncClient(headers={'X-Production-Line': 'welding'}, transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
            tasks = [asyncio.create_task(client.get('/api/v1/welding/parts/1')) for _ in range(4)]
            try:
                with anyio.fail_after(2):
                    while counts['running'] != 4:
                        await anyio.sleep(0.001)
                with anyio.fail_after(1):
                    response = await client.get('/api/v1/welding/jobs?start=2026-10-01&end=2026-10-02')
                assert response.status_code == 200 and response.json()['data']['data_version'] == 'ready'
                assert not any(task.done() for task in tasks)
            finally:
                release.set()
                await asyncio.gather(*tasks)
    anyio.run(exercise)


@pytest.mark.parametrize('format,media', [('csv', 'text/csv'), ('html', 'text/html')])
def test_http_csv_and_html_export_preserve_data_without_pdf(api, format, media):
    database_services(api.app).lines['welding'].service.report = lambda *args, **kwargs: report_data()
    api.app.state.runtime.pdf_service = SimpleNamespace(render=lambda _: pytest.fail('CSV/HTML must not render PDF'))
    result = api.get(f'/api/v1/welding/export?report_type=jobs&format={format}&start=2026-10-01&end=2026-10-31')
    assert result.status_code == 200 and media in result.headers['content-type']
    assert f'.{format}' in result.headers['content-disposition']
    assert '工件流水号' in result.content.decode('utf-8-sig')


def test_refresh_control_is_validated_and_excluded_from_business_filters(api):
    calls = []
    database_services(api.app).lines['welding'].service.page = lambda kind, selected, **kwargs: calls.append((selected, kwargs)) or {'items': []}
    url = '/api/v1/welding/jobs?start=2026-10-01&end=2026-10-02'
    assert api.get(url + '&refresh=true').status_code == 200
    assert calls[-1][1] == {'force': True}
    assert api.get(url + '&refresh=false').status_code == 200
    assert calls[-1][1] == {}
    assert api.get(url + '&refresh=unexpected').status_code == 422
    assert api.get(url + '&refresh=true&refresh=true').status_code == 422
