from pathlib import Path
import sqlite3

import pytest

from app.database.sqlite import SQLiteDatabase
from app.database.migrations import apply_migrations
from app.database.migrations.runner import run_migrations, execute_script
from app.database.migrations.registry import MIGRATIONS


def test_fresh_schema_and_repeat_startup_keep_one_ordered_version_per_upgrade(tmp_path):
    database = SQLiteDatabase(tmp_path / 'metadata.db')
    apply_migrations(database)
    with database.connect() as connection:
        first = [tuple(row) for row in connection.execute('SELECT name, applied_at FROM schema_migrations ORDER BY name')]
        assert [row[0] for row in first] == [name for name, _ in MIGRATIONS]
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    apply_migrations(database)
    with database.connect() as connection:
        assert [tuple(row) for row in connection.execute('SELECT name, applied_at FROM schema_migrations ORDER BY name')] == first
    assert not list((tmp_path / 'backups').glob('*.db'))


def test_existing_data_is_backed_up_and_schema_and_markers_roll_back_together(tmp_path):
    database = SQLiteDatabase(tmp_path / 'metadata.db')
    with database.connect() as connection:
        connection.execute('CREATE TABLE records(value TEXT)')
        connection.execute("INSERT INTO records VALUES ('preserved')")

    def fail(connection):
        execute_script(connection, "CREATE TABLE new_table(value TEXT);\nINSERT INTO records VALUES ('rolled back');\n")
        raise RuntimeError('failed upgrade')

    with pytest.raises(RuntimeError, match='failed upgrade'):
        run_migrations(database, [('001_failing', fail)])
    with database.connect() as connection:
        assert [row[0] for row in connection.execute('SELECT value FROM records')] == ['preserved']
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name IN ('new_table', 'schema_migrations')").fetchall()
    backups = list((tmp_path / 'backups').glob('*.db'))
    assert len(backups) == 1
    assert backups[0].stat().st_mode & 0o077 == 0
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('SELECT value FROM records').fetchall() == [('preserved',)]


def test_legacy_database_adopts_baseline_without_losing_records(tmp_path):
    database = SQLiteDatabase(tmp_path / 'metadata.db')
    with database.connect() as connection:
        connection.execute('CREATE TABLE custom_history(id TEXT PRIMARY KEY)')
        connection.execute("INSERT INTO custom_history VALUES ('kept')")
        connection.execute('CREATE TABLE schema_migrations(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
        connection.execute("INSERT INTO schema_migrations VALUES ('device-isolation-v2', 'original-time')")
    apply_migrations(database)
    with database.connect() as connection:
        assert connection.execute('SELECT id FROM custom_history').fetchone()[0] == 'kept'
        assert connection.execute("SELECT applied_at FROM schema_migrations WHERE name='device-isolation-v2'").fetchone()[0] == 'original-time'
    assert len(list((tmp_path / 'backups').glob('*.db'))) == 1


def test_bad_foreign_key_upgrade_rolls_back(tmp_path):
    database = SQLiteDatabase(tmp_path / 'metadata.db')
    def invalid(connection):
        execute_script(connection, 'CREATE TABLE parent(id INTEGER PRIMARY KEY);\nCREATE TABLE child(parent_id INTEGER REFERENCES parent(id));\nINSERT INTO child VALUES (99);\n')
    with pytest.raises(ValueError, match='外键'):
        run_migrations(database, [('001_invalid', invalid)])
    with database.connect() as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='child'").fetchall()


def test_migration_cli_status_does_not_create_database_and_upgrade_is_repeatable(tmp_path):
    import subprocess
    import sys
    script = Path(__file__).resolve().parents[2] / 'scripts/migrate_database.py'
    path = tmp_path / 'metadata.db'
    command = [sys.executable, str(script)]
    status = subprocess.run([*command, 'status', '--database', str(path)], capture_output=True, text=True, check=True)
    assert status.stdout.count('pending ') == len(MIGRATIONS)
    assert not path.exists()
    upgraded = subprocess.run([*command, 'upgrade', '--database', str(path)], capture_output=True, text=True, check=True)
    assert upgraded.stdout.count('applied ') == len(MIGRATIONS)
    repeated = subprocess.run([*command, 'upgrade', '--database', str(path)], capture_output=True, text=True, check=True)
    assert repeated.stdout == upgraded.stdout


@pytest.mark.parametrize('owner', ['web', 'worker'])
def test_manual_upgrade_refuses_active_processes(tmp_path, owner):
    import subprocess
    import sys
    from time import time
    from app.services.process_lock import FileLock
    path = tmp_path / 'metadata.db'
    database = SQLiteDatabase(path)
    apply_migrations(database)
    script = Path(__file__).resolve().parents[2] / 'scripts/migrate_database.py'
    lock = FileLock(tmp_path / '.web-process.lock')
    if owner == 'web':
        lock.acquire()
    else:
        with database.connect() as connection:
            connection.execute("INSERT INTO process_leases(name,owner,expires_at,heartbeat_at) VALUES ('worker','active',?,?)", (time() + 60, time()))
    try:
        result = subprocess.run([sys.executable, str(script), 'upgrade', '--database', str(path)], capture_output=True, text=True)
        assert result.returncode != 0
        assert ('Web' if owner == 'web' else 'Worker') in result.stderr
        assert not list((tmp_path / 'backups').glob('*.db'))
    finally:
        lock.release()
