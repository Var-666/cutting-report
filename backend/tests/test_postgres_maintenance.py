"""PG-only maintenance CLI; isolated DBs, no SQLite import fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts/manage_postgres.py'


def command(cfg, *args):
    env = {**os.environ, 'POSTGRES_HOST': cfg.postgres_host, 'POSTGRES_PORT': str(cfg.postgres_port),
        'POSTGRES_DATABASE': cfg.postgres_database, 'POSTGRES_USER': cfg.postgres_user,
        'POSTGRES_PASSWORD': cfg.postgres_password}
    # Maintenance must not open any historical SQLite file, even if one remains on disk.
    code = "import runpy, sqlite3; sqlite3.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('SQLite opened')); runpy.run_path(" + repr(str(SCRIPT)) + ", run_name='__main__')"
    return subprocess.run([sys.executable, '-c', code, *args], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=20)


def test_schema_status_and_consumer_maintenance_preserve_pg_records(postgres_state):
    database, _, migrator = postgres_state
    with database.connect() as connection:
        connection.execute('INSERT INTO llm_usage_totals(id) VALUES (1)')
    for action in ('schema', 'status', 'prepare-consumers', 'status'):
        result = command(migrator, action)
        assert result.returncode == 0, result.stderr
        assert migrator.postgres_password not in result.stdout + result.stderr
        payload = json.loads(result.stdout)
        if action == 'prepare-consumers':
            assert payload['consumer_cutover'] == dict(background=0, reports=0, energy=0, unverified_source=0)
        else:
            assert len(payload['postgres_versions']) == 6
    with database.connect() as connection:
        assert connection.execute('SELECT id FROM llm_usage_totals').fetchall() == [{'id': 1}]
    assert command(postgres_state[1], 'status').returncode == 0


@pytest.mark.parametrize('action', ['schema', 'prepare-consumers'])
def test_runtime_account_cannot_modify_schema_or_prepare_consumers(postgres_state, action):
    result = command(postgres_state[1], action)
    assert result.returncode == 1
    assert 'cutting_migrator' in result.stderr
    assert postgres_state[1].postgres_password not in result.stdout + result.stderr


@pytest.mark.parametrize('args', [('check',), ('migrate',), ('verify',), ('status', '--sqlite', 'history.db')])
def test_removed_sqlite_commands_are_rejected(args):
    result = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2
