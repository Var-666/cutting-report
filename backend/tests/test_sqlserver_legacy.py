"""Exercise the real query subprocess with an offline ODBC driver substitute."""
from datetime import datetime
from decimal import Decimal
import os
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest
from pydantic import ValidationError

from app.core.report_deadline import ReportOperationTimeout, operation_deadline
from app.core.request_cancellation import (
    SqlRequestCancelled, bind_request_id, reset_request_id, sql_request_cancellations,
)
from app.database import sqlserver_legacy
from app.database.source_registry import DatabaseSourceRegistry, source_configs_json
from app.database.sqlserver import SqlServerClient, SqlServerConfigurationError
from app.models.database_sources import DatabaseSourceConfig


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    (tmp_path / 'pyodbc.py').write_text('''
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from time import sleep
class Cursor:
    description = [('number',), ('stamp',), ('data',), ('policy',), ('parameter',)]
    def execute(self, sql, parameters):
        self.parameters = parameters
        if sql == 'fail':
            raise RuntimeError('private-password; UID=reader')
        if sql == 'slow':
            Path(os.environ['QUERY_STARTED']).touch()
            sleep(30)
    def fetchall(self):
        return [(Decimal('1.2500'), datetime(2026, 11, 3, 8), b'bytes',
                 Path(os.environ['OPENSSL_CONF']).read_text(), self.parameters[0])]
    def close(self): pass
    def cancel(self): pass
class Connection:
    def cursor(self): return Cursor()
    def close(self): Path(os.environ['QUERY_CLOSED']).touch()
def connect(connection_string, **options):
    assert options == {'readonly': True}
    assert 'PWD=private-password' in connection_string
    return Connection()
''')
    backend = Path(__file__).resolve().parents[1]
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join((str(tmp_path), str(backend))))
    monkeypatch.setenv('QUERY_STARTED', str(tmp_path / 'started'))
    monkeypatch.setenv('QUERY_CLOSED', str(tmp_path / 'closed'))
    processes = []
    popen = sqlserver_legacy.subprocess.Popen
    def start(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(sqlserver_legacy.subprocess, 'Popen', start)
    config = DatabaseSourceConfig(id='bm', name='BM', engine='sqlserver', host='offline', port=1433,
        database='bm', username='reader', password='private-password', legacy_tls=True)
    yield SqlServerClient(config), processes, tmp_path
    assert all(process.poll() is not None for process in processes)
    assert all(process.stdin.closed and process.stdout.closed for process in processes)


def test_isolated_query_preserves_values_credentials_and_parent_tls(legacy):
    client, processes, path = legacy
    original_policy = os.environ.get('OPENSSL_CONF')
    rows = client.fetch_all('SELECT value WHERE key = ?', ('CMYZ0170',))
    assert len(rows) == 1
    assert rows[0]['number'] == Decimal('1.2500')
    assert rows[0]['stamp'] == datetime(2026, 11, 3, 8)
    assert rows[0]['data'] == b'bytes' and rows[0]['parameter'] == 'CMYZ0170'
    assert 'SECLEVEL=0' in rows[0]['policy'] and 'MinProtocol = TLSv1' in rows[0]['policy']
    assert os.environ.get('OPENSSL_CONF') == original_policy
    assert 'private-password' not in repr(processes[0].args)
    assert (path / 'closed').exists()


def test_isolated_query_failure_is_redacted_and_disabled_source_does_not_spawn(legacy):
    client, processes, _ = legacy
    with pytest.raises(RuntimeError, match='RuntimeError') as caught:
        client.fetch_all('fail', ())
    assert 'private-password' not in str(caught.value) and 'reader' not in str(caught.value)
    disabled = SqlServerClient(client.settings.model_copy(update={'enabled': False}))
    with pytest.raises(SqlServerConfigurationError, match='未启用'):
        disabled.fetch_all('SELECT 1', ())
    assert len(processes) == 1
    with pytest.raises(SqlServerConfigurationError, match='隔离进程'):
        with client.connect():
            pytest.fail('legacy TLS must never initialize in the parent')


def test_deadline_kills_child_and_preserves_timeout_contract(legacy):
    client, processes, _ = legacy
    started = monotonic()
    with operation_deadline(0.2), pytest.raises(ReportOperationTimeout):
        client.fetch_all('slow', ())
    assert monotonic() - started < 3 and len(processes) == 1


def test_connection_and_query_timeout_bounds_child_without_a_report_deadline(legacy):
    client, processes, _ = legacy
    client = SqlServerClient(client.settings.model_copy(update={'connect_timeout': 1, 'query_timeout': 1}))
    started = monotonic()
    with pytest.raises(TimeoutError, match='连接或查询超时'):
        client.fetch_all('slow', ())
    assert monotonic() - started < 5 and len(processes) == 1


def test_cancellation_kills_running_child_and_does_not_start_pre_cancelled_query(legacy):
    client, processes, path = legacy
    execution_id = sql_request_cancellations.activate('cancel-legacy')
    token = bind_request_id(execution_id)
    stop = Event()
    def cancel():
        while not stop.wait(0.01):
            if (path / 'started').exists():
                sql_request_cancellations.cancel(execution_id)
                return
    thread = Thread(target=cancel)
    thread.start()
    try:
        with operation_deadline(5), pytest.raises(SqlRequestCancelled):
            client.fetch_all('slow', ())
        with pytest.raises(SqlRequestCancelled):
            client.fetch_all('SELECT 1', ())
        assert len(processes) == 1
    finally:
        stop.set()
        thread.join()
        reset_request_id(token)
        sql_request_cancellations.finish(execution_id)


def test_source_compatibility_defaults_to_off_roundtrips_and_rejects_mysql():
    values = dict(id='bm', name='BM', engine='sqlserver', host='offline', port=1433,
                  database='bm', username='reader', password='private-password')
    assert not DatabaseSourceConfig(**values).legacy_tls
    config = DatabaseSourceConfig(**values, legacy_tls=True)
    registry = DatabaseSourceRegistry.from_json(source_configs_json([config]))
    assert registry.get('bm').legacy_tls and registry.view('bm').legacy_tls
    assert 'password' not in registry.view('bm').model_dump()
    with pytest.raises(ValidationError, match='仅适用于 SQL Server'):
        DatabaseSourceConfig(**{**values, 'engine': 'mysql'}, legacy_tls=True)
