import sqlite3

import pytest

from tests.line_fixtures import CUTTING_SCOPES
from app.database.device_migrations import migrate_device_isolation, rebuild_table
from app.database.sqlite_history import SQLiteHistory
from app.database.migrations import apply_migrations
STAMP='2026-10-12T12:00:00+08:00'
DAY='2026-10-12'


@pytest.fixture
def legacy(tmp_path):
    db = SQLiteHistory(tmp_path / 'metadata.db')
    apply_migrations(db)
    with db.connect() as connection:
        connection.execute("INSERT INTO users(user_id,username,display_name,password_hash,role,created_at,updated_at) VALUES('admin','admin','管理员','hash','admin',?,?)",(STAMP,STAMP))
        connection.execute("INSERT INTO workshop_reports(report_id,report_date,source_mode,scope_machine_no,item_count,generated_by_id,generated_by_name,generated_at,created_at) VALUES('r',?,'sqlserver','Cutter01',1,'admin','管理员',?,?)",(DAY,STAMP,STAMP))
        connection.execute("INSERT INTO workshop_report_items(report_item_id,report_id,task_id,ordinal,snapshot_revision) VALUES('i','r','same',1,1)")
        connection.execute("INSERT INTO workshop_confirmations(task_id,worker_id,worker_name,report_id,created_at,confirmed_at,updated_at,scope_machine_no) VALUES('same','admin','管理员','r',?,?,?,'Cutter01')",(STAMP,STAMP,STAMP))
        connection.execute("INSERT INTO workshop_confirmation_audits(audit_id,task_id,action,operated_by_id,operated_by_name,operated_by_role,operated_at,scope_machine_no,report_id,reason) VALUES('audit','same','confirm','admin','管理员','admin',?,'Cutter01','r','确认')",(STAMP,))
        connection.execute("INSERT INTO alarm_sync_state(source_name) VALUES('production')")
    # Recreate the previous release's global task primary key and default.
    rebuild_table(db, 'workshop_confirmations', lambda sql: sql.replace('task_id TEXT NOT NULL', 'task_id TEXT PRIMARY KEY').replace('PRIMARY KEY(scope_machine_no, task_id),', '').replace('scope_machine_no TEXT NOT NULL', "scope_machine_no TEXT NOT NULL DEFAULT 'Cutter01'"))
    with db.connect() as connection:
        connection.execute("UPDATE alarm_sync_state SET last_alarm_id=123, lease_owner='owner', lease_until='2099-01-01', open_recheck_after_id=9, history_recheck_after_id=8, monitor_start_at='2026-09-29 00:00:00', last_success_at=? WHERE source_name='production'", (STAMP,))
    return db, {'user_id':'admin'}, {'report_id':'r'}


def dump(db):
    with db.connect() as connection:
        return list(connection.iterdump())


def test_migration_preserves_records_cursor_lease_and_is_idempotent(legacy):
    db, workshop, admin = legacy
    with db.connect() as connection:
        before = dict(connection.execute("SELECT * FROM alarm_sync_state WHERE source_name='production'").fetchone())
        audits = [dict(row) for row in connection.execute('SELECT * FROM workshop_confirmation_audits')]
    migrate_device_isolation(db, CUTTING_SCOPES)
    with db.connect() as connection:
        after = dict(connection.execute("SELECT * FROM alarm_sync_state WHERE source_name='production:Cutter01'").fetchone())
        assert after == {**before, 'source_name': 'production:Cutter01'}
        assert [dict(row) for row in connection.execute('SELECT * FROM workshop_confirmation_audits')] == audits
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='workshop_confirmations'").fetchone()[0]
        assert "DEFAULT 'Cutter01'" not in schema
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM workshop_confirmations WHERE scope_machine_no='Cutter01'").fetchone()[0] == 1
    copies = list((db.path.parent / 'backups').glob('*-before-device-isolation-*.db'))
    assert len(copies) == 1
    with sqlite3.connect(copies[0]) as backup:
        assert backup.execute("SELECT last_alarm_id,lease_owner FROM alarm_sync_state WHERE source_name='production'").fetchone() == (123, 'owner')
    snapshot = dump(db)
    migrate_device_isolation(db, CUTTING_SCOPES)
    assert dump(db) == snapshot
    assert list((db.path.parent / 'backups').glob('*-before-device-isolation-*.db')) == copies
    with db.connect() as connection:
        connection.execute("INSERT INTO workshop_confirmations(task_id,worker_id,worker_name,created_at,confirmed_at,updated_at,scope_machine_no) VALUES('same','admin','管理员',?,?,?,'Cutter02')",(STAMP,STAMP,STAMP))
        assert connection.execute('SELECT COUNT(*) FROM workshop_confirmations').fetchone()[0] == 2



@pytest.mark.parametrize('fault', ['cursor_collision', 'owner_conflict', 'unknown_owner', 'foreign_key'])
def test_migration_failure_rolls_back_every_change_and_keeps_backup(legacy, fault):
    db, workshop, admin = legacy
    with db.connect() as connection:
        if fault == 'cursor_collision':
            connection.execute("INSERT INTO alarm_sync_state(source_name,last_alarm_id) VALUES ('production:Cutter01',999)")
        elif fault == 'owner_conflict':
            connection.execute("UPDATE workshop_confirmations SET scope_machine_no='Cutter02'")
        elif fault == 'unknown_owner':
            connection.execute("UPDATE workshop_confirmations SET report_id=NULL,scope_machine_no='missing'")
        else:
            connection.execute('PRAGMA foreign_keys=OFF')
            connection.execute("UPDATE workshop_confirmations SET worker_id='deleted-worker'")
    before = dump(db)
    with pytest.raises(ValueError, match='已回滚'):
        migrate_device_isolation(db, CUTTING_SCOPES)
    assert dump(db) == before
    assert len(list((db.path.parent / 'backups').glob('*-before-device-isolation-*.db'))) == 1


def test_single_device_schema_adds_device_ids_without_changing_history(tmp_path):
    db = SQLiteHistory(tmp_path / 'metadata.db')
    with db.connect() as connection:
        connection.executescript('''
            CREATE TABLE report_jobs(report_id TEXT PRIMARY KEY);
            INSERT INTO report_jobs VALUES ('old');
            CREATE TABLE analysis_source_versions(report_type TEXT, report_date TEXT, data_version TEXT, updated_at TEXT, PRIMARY KEY(report_type, report_date));
            INSERT INTO analysis_source_versions VALUES ('workshop','2026-10-12','v1','old-time');
            CREATE TABLE workshop_reports(report_id TEXT PRIMARY KEY, report_date TEXT NOT NULL UNIQUE, scope_machine_no TEXT);
            INSERT INTO workshop_reports VALUES ('r','2026-10-12',NULL);
            CREATE TABLE workshop_report_items(report_item_id TEXT PRIMARY KEY,report_id TEXT,task_id TEXT);
            INSERT INTO workshop_report_items VALUES ('i','r','task');
            CREATE TABLE workshop_confirmations(task_id TEXT PRIMARY KEY,report_id TEXT,worker_id TEXT);
            INSERT INTO workshop_confirmations VALUES ('task','r','worker');
            CREATE TABLE workshop_confirmation_audits(audit_id TEXT PRIMARY KEY,task_id TEXT,report_id TEXT);
            INSERT INTO workshop_confirmation_audits VALUES ('a','task','r');
        ''')
    migrate_device_isolation(db, CUTTING_SCOPES)
    with db.connect() as connection:
        for table, column in [('report_jobs','device_id'), ('analysis_source_versions','device_id'), ('workshop_reports','scope_machine_no'), ('workshop_confirmations','scope_machine_no'), ('workshop_confirmation_audits','scope_machine_no')]:
            assert connection.execute(f'SELECT {column} FROM {table}').fetchone()[0] == 'Cutter01'
        assert connection.execute('SELECT data_version,updated_at FROM analysis_source_versions').fetchone()[:] == ('v1','old-time')


def test_unattributable_orphan_is_not_guessed_as_default(tmp_path):
    db = SQLiteHistory(tmp_path / 'metadata.db')
    with db.connect() as connection:
        connection.execute('CREATE TABLE workshop_confirmations(task_id TEXT PRIMARY KEY, report_id TEXT)')
        connection.execute("INSERT INTO workshop_confirmations VALUES ('orphan',NULL)")
    before = dump(db)
    with pytest.raises(ValueError, match='归属不明确'):
        migrate_device_isolation(db, CUTTING_SCOPES)
    assert dump(db) == before
