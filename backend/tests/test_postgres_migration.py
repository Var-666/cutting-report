"""Real isolated PostgreSQL integration checks, opt in with P4_TEST_POSTGRES=1."""
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
from time import time
from uuid import uuid4
import multiprocessing as mp
import subprocess
import sys

import pytest

from app.core.process_lock import FileLock
from app.database.migrations import apply_migrations as sqlite_migrations
from app.database.sqlite_history import SQLiteHistory
from app.database.postgres import PostgresDatabase
from app.database.postgres_migrations import apply_migrations, validate_schema, MigrationError, DIRECTORY
from app.database.sqlite_to_postgres import check_source, sqlite_schema, table_order, offline_snapshot, transfer
from tests.line_fixtures import configuration

pytestmark = pytest.mark.skipif(os.environ.get('P4_TEST_POSTGRES')!='1', reason='requires explicitly enabled isolated PostgreSQL')


@pytest.fixture
def postgres(tmp_path):
    import psycopg
    from psycopg import sql
    name = 'cutting_p4_'+uuid4().hex
    options = dict(host='127.0.0.1',port=int(os.environ.get('P4_TEST_POSTGRES_PORT','55434')),
                   user='postgres',password=os.environ.get('P4_TEST_POSTGRES_PASSWORD','p4-isolated-test-only'))
    with psycopg.connect(**options,dbname=os.environ.get('P4_TEST_POSTGRES_DATABASE','p4_test'),autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    cfg = configuration(tmp_path,postgres_host=options['host'],postgres_port=options['port'],postgres_database=name,
        postgres_user=options['user'],postgres_password=options['password'],postgres_pool_max_size=1)
    with PostgresDatabase(cfg) as database:
        yield database, cfg, options
    with psycopg.connect(**options,dbname=os.environ.get('P4_TEST_POSTGRES_DATABASE','p4_test'),autocommit=True) as admin:
        admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.fixture
def source(tmp_path):
    path = tmp_path/'source.db'
    sqlite_migrations(SQLiteHistory(path))
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        profile = sqlite_schema(connection)
        for table in reversed(table_order(profile)):
            connection.execute(f'DELETE FROM "{table}"')
        inserted = {}
        for table in table_order(profile):
            values = {}
            for col,meta in profile[table]['columns'].items():
                if meta['notnull'] or meta['pk']:
                    values[col] = 1 if meta['type']=='INTEGER' else 1793740000.123456 if meta['type']=='REAL' else col+'-历史-\'值'
                else: values[col] = None
                if col.endswith('_json') or col=='payload': values[col] = '{"历史":"原值", "number":1.25}'
                if 'device_id'==col or 'line_id'==col: values[col] = 'Cutter02'
                if col=='role': values[col]='worker'
                if col=='action': values[col]='grant' if table=='admin_role_audits' else 'confirm'
                if col=='trigger_type': values[col]='manual'
                if col=='frequency': values[col]='daily'
                if col=='report_type': values[col]='workshop'
                if col=='status':
                    values[col]={'alarm_incidents':'open','alarm_reports':'completed','cutting_dispatches':'sent',
                                 'cutting_downloads':'failed','llm_usage_calls':'responded'}.get(table,'completed')
            for fk in profile[table]['foreign_keys']:
                values[fk['from']] = inserted[fk['table']][fk['to']]
            if table=='llm_usage_calls': values['id']=2**40
            if table=='users': values.update(external_id='ext-001',auth_source='hr_database')
            if table=='reports': values.update(relative_path='fixture.html',file_size=2**40)
            if table=='cutting_downloads': values['payload']='{"status":"failed"}'
            columns=list(values)
            quoted=','.join('"'+c+'"' for c in columns)
            connection.execute(f'INSERT INTO "{table}" ({quoted}) VALUES ({",".join("?" for _ in columns)})',tuple(values.values()))
            inserted[table]=values
        connection.execute('UPDATE alarm_incidents SET pdf_report_id=?',(inserted['alarm_reports']['report_id'],))
        for index in range(8):
            row=inserted['users'] | dict(user_id=f'extra-{index}',username=f'user-{index}',external_id=None)
            connection.execute(f'INSERT INTO users VALUES ({",".join("?" for _ in row)})',tuple(row[c] for c in profile['users']['columns']))
    (tmp_path/'reports').mkdir()
    (tmp_path/'reports'/'fixture.html').write_text('offline fixture')
    return path


def perform(path,database,action='migrate',**changes):
    with offline_snapshot(path,path.parent/'backups') as (snapshot,backup):
        result=transfer(snapshot,database,action=action,batch_size=2,reports_root=path.parent/'reports',data_root=path.parent/'data',**changes)
        return result,backup


def test_pool_commit_rollback_health_and_return(postgres):
    database,_,_=postgres
    assert database.healthy()
    with database.connect() as connection:
        pid=connection.info.backend_pid
        connection.execute('CREATE TABLE public.pool_fixture(id bigint PRIMARY KEY)')
        connection.execute('INSERT INTO public.pool_fixture VALUES (1)')
    with pytest.raises(RuntimeError):
        with database.connect() as connection:
            assert connection.info.backend_pid==pid
            connection.execute('INSERT INTO public.pool_fixture VALUES (2)')
            raise RuntimeError('rollback')
    with database.connect() as connection:
        assert connection.execute('SELECT id FROM public.pool_fixture').fetchall()==[{'id':1}]
    assert database.pool.get_stats()['pool_size']==1


def test_schema_all_tables_constraints_indexes_and_repeated_install(postgres):
    database,_,_=postgres
    first=apply_migrations(database)
    assert first==apply_migrations(database)
    with database.connect() as connection:
        assert validate_schema(connection)==first
        tables={r['tablename'] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='app_state'")}
        expected=set(json.loads((DIRECTORY/'sqlite_schema.json').read_text()))-{'schema_migrations'}
        assert tables==expected|{'_postgres_schema_migrations','_sqlite_imports','_sqlite_schema_versions','workshop_source_correction_audits'}
        assert connection.execute("SELECT count(*) AS n FROM pg_indexes WHERE schemaname='app_state'").fetchone()['n']==103
        connection.execute('DROP INDEX idx_background_tasks_ready')
    with pytest.raises(MigrationError,match='结构漂移'): apply_migrations(database)


def test_failed_schema_transaction_leaves_no_published_version(postgres,tmp_path,monkeypatch):
    from app.database import postgres_migrations
    database,_,_=postgres
    (tmp_path/'001_initial.sql').write_text('CREATE TABLE partial(id bigint); SELECT missing_column FROM partial;')
    monkeypatch.setattr(postgres_migrations,'DIRECTORY',tmp_path)
    with pytest.raises(Exception): apply_migrations(database)
    with database.connect() as connection:
        assert connection.execute("SELECT schema_name FROM information_schema.schemata WHERE schema_name='app_state'").fetchone() is None


def _install_in_process(cfg, channel):
    try:
        with PostgresDatabase(cfg) as database:
            channel.send(apply_migrations(database))
    except Exception as exc: channel.send(type(exc).__name__)
    finally: channel.close()


def test_schema_installation_is_serialized_between_processes(postgres):
    database,cfg,_=postgres
    context=mp.get_context('spawn')
    channels=[context.Pipe() for _ in range(2)]
    processes=[context.Process(target=_install_in_process,args=(cfg,child)) for _,child in channels]
    try:
        for process in processes: process.start()
        for parent,_ in channels:
            assert parent.poll(15)
            assert parent.recv()==apply_migrations(database)
    finally:
        for process in processes:
            process.join(5)
            if process.is_alive(): process.kill();process.join(5)
        for parent,child in channels: parent.close();child.close()


def test_all_state_migrates_in_batches_with_ids_values_relations_and_history(postgres,source):
    database,_,_=postgres
    apply_migrations(database)
    ready,_=perform(source,database,'check')
    assert ready['result']=='ready'
    result,backup=perform(source,database)
    assert result['table_count']==38 and result['total_rows']==46
    assert result['files']=={'checked':1,'missing':0,'retained_deleted_downloads':0}
    assert backup.exists() and backup.stat().st_mode&0o777==0o600
    again,_=perform(source,database)
    assert again==result
    verified,_=perform(source,database,'verify')
    assert verified==result
    with database.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM _sqlite_schema_versions').fetchone()['n']==16
        assert connection.execute('SELECT count(*) AS n FROM _postgres_schema_migrations').fetchone()['n']==6
        assert connection.execute('SELECT file_location FROM cutting_dispatches').fetchone()=={'file_location':None}
        assert connection.execute('SELECT file_size,relative_path FROM reports').fetchone()=={'file_size':2**40,'relative_path':'fixture.html'}
        assert connection.execute("SELECT nextval('llm_usage_calls_id_seq') AS n").fetchone()['n']==2**40+1
        connection.execute("UPDATE users SET display_name='tampered'")
    with pytest.raises(MigrationError,match='摘要不一致'): perform(source,database,'verify')


def test_unknown_target_is_not_overwritten_and_different_source_is_rejected(postgres,source):
    database,_,_=postgres
    apply_migrations(database)
    with database.connect() as connection:
        connection.execute("INSERT INTO analysis_generations VALUES('unknown','attempt','failed',0,NULL,NULL)")
    with pytest.raises(MigrationError,match='来源不明'): perform(source,database)
    with database.connect() as connection: connection.execute('DELETE FROM analysis_generations')
    perform(source,database)
    with sqlite3.connect(source) as connection: connection.execute("UPDATE users SET display_name='changed source'")
    with pytest.raises(MigrationError,match='不同源快照'): perform(source,database)


def test_connection_loss_mid_import_rolls_back_rows_and_keeps_backup(postgres,source,monkeypatch):
    import psycopg
    from app.database import sqlite_to_postgres as module
    database,cfg,options=postgres
    apply_migrations(database)
    original=module.batches
    original_connect=database.connect
    pid=[]
    @contextmanager
    def connect():
        with original_connect() as connection:
            pid.append(connection.info.backend_pid)
            yield connection
    monkeypatch.setattr(database,'connect',connect)
    def interrupted(*args,**kwargs):
        for batch in original(*args,**kwargs):
            yield batch
            if args[1]=='users' and not kwargs.get('postgres') and pid:
                with psycopg.connect(**options,dbname=cfg.postgres_database,autocommit=True) as killer:
                    killer.execute('SELECT pg_terminate_backend(%s)',(pid[-1],))
    monkeypatch.setattr(module,'batches',interrupted)
    with pytest.raises(MigrationError,match='导入失败'): perform(source,database)
    monkeypatch.setattr(module,'batches',original)
    with database.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM _sqlite_imports').fetchone()['n']==0
        assert connection.execute('SELECT count(*) AS n FROM admin_role_audits').fetchone()['n']==0
    assert list((source.parent/'backups').glob('*p4*.db'))
    assert perform(source,database)[0]['result']=='verified'


def test_preflight_refuses_writers_leases_schema_drift_and_missing_files(postgres,source):
    database,_,_=postgres
    apply_migrations(database)
    with FileLock(source.parent/'.web-process.lock'), pytest.raises(MigrationError,match='停止'):
        perform(source,database)
    with sqlite3.connect(source) as writer:
        writer.execute('BEGIN IMMEDIATE')
        with pytest.raises(MigrationError,match='写入进程'): perform(source,database)
    with sqlite3.connect(source) as connection:
        connection.execute('UPDATE process_leases SET expires_at=?',(time()+60,))
    with pytest.raises(MigrationError,match='有效进程租约'): perform(source,database)
    with sqlite3.connect(source) as connection: connection.execute('UPDATE process_leases SET expires_at=0')
    (source.parent/'reports'/'fixture.html').unlink()
    with pytest.raises(MigrationError,match='缺失'): perform(source,database)
    with sqlite3.connect(source) as connection: connection.execute('CREATE TABLE unknown(id TEXT PRIMARY KEY)')
    with pytest.raises(MigrationError,match='Schema'): perform(source,database)


def test_source_accepts_known_device_marker_but_refuses_unknown_history(source):
    with sqlite3.connect(source) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("INSERT INTO schema_migrations VALUES ('device-isolation-v2','original-time')")
        _, versions = check_source(connection)
        assert versions[-1] == {'name':'device-isolation-v2','applied_at':'original-time'}
        connection.execute("INSERT INTO schema_migrations VALUES ('unknown-migration','test-time')")
        with pytest.raises(MigrationError, match='迁移历史'):
            check_source(connection)


def historical_source(path, *, audits=True):
    from app.database.device_migrations import rebuild_table
    rebuild_table(SQLiteHistory(path), 'report_analyses',
                  lambda ddl: ddl.replace('data_version TEXT NOT NULL','data_version TEXT'))
    if audits:
        with sqlite3.connect(path) as connection:
            connection.executescript('''CREATE TABLE workshop_source_correction_audits (
                audit_id TEXT PRIMARY KEY, report_id TEXT NOT NULL, task_id TEXT NOT NULL,
                payload TEXT NOT NULL, reason TEXT NOT NULL, operated_by_id TEXT NOT NULL,
                operated_by_name TEXT NOT NULL, operated_at TEXT NOT NULL);
                CREATE INDEX idx_workshop_source_audits_report
                  ON workshop_source_correction_audits(report_id,operated_at DESC);
                CREATE INDEX idx_workshop_source_audits_retention
                  ON workshop_source_correction_audits(operated_at,audit_id);''')
            connection.execute('INSERT INTO workshop_source_correction_audits VALUES (?,?,?,?,?,?,?,?)',
                               ('historical-audit','retired-report','42','{"old":"原值"}','历史更正','old-user','旧管理员','2026-09-30T12:00:00+08:00'))


@pytest.mark.parametrize('audits',[False,True])
def test_known_nullable_version_and_retired_audits_migrate_without_row_changes(postgres,source,audits):
    database,_,_=postgres
    historical_source(source,audits=audits)
    apply_migrations(database)
    result,_=perform(source,database)
    assert result['result']=='verified' and result['table_count']==38+int(audits)
    assert perform(source,database,action='verify')[0]==result
    if audits:
        with sqlite3.connect(source) as original, database.connect() as target:
            original.row_factory=sqlite3.Row
            assert target.execute('SELECT * FROM workshop_source_correction_audits').fetchall()==[
                dict(row) for row in original.execute('SELECT * FROM workshop_source_correction_audits')]


def test_nullable_declaration_does_not_allow_real_null_versions(postgres,source):
    database,_,_=postgres
    historical_source(source)
    with sqlite3.connect(source) as connection:connection.execute('UPDATE report_analyses SET data_version=NULL')
    apply_migrations(database)
    with pytest.raises(MigrationError,match='真实空值'):perform(source,database)
    with database.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM report_analyses').fetchone()['n']==0


def test_known_audit_name_does_not_allow_unknown_columns(postgres,source):
    database,_,_=postgres
    historical_source(source)
    with sqlite3.connect(source) as connection:connection.execute('ALTER TABLE workshop_source_correction_audits ADD COLUMN unexpected TEXT')
    apply_migrations(database)
    with pytest.raises(MigrationError,match='Schema'):perform(source,database)


def test_absent_source_audits_cannot_hide_existing_target_history(postgres,source):
    database,_,_=postgres
    apply_migrations(database)
    with database.connect() as connection:
        connection.execute("INSERT INTO workshop_source_correction_audits VALUES ('unexpected','r','t','{}','retained','u','n','original-time')")
    with pytest.raises(MigrationError,match='源库不存在的历史记录'):perform(source,database,action='check')


def test_archive_migration_appends_to_existing_003_without_changing_state(postgres,monkeypatch):
    import hashlib
    import app.database.postgres_migrations as migrations
    database,_,_=postgres
    def original_schema(connection):
        digest=hashlib.sha256(json.dumps(migrations.catalog(connection),sort_keys=True).encode()).hexdigest()
        assert digest=='5188665e353111c6564ac52223e9efe6f9aca517d4c608ad5227d576f5171495'
        return connection.execute('SELECT version,checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
    with monkeypatch.context() as patch:
        patch.setattr(migrations,'VERSIONS',migrations.VERSIONS[:3])
        patch.setattr(migrations,'validate_schema',original_schema)
        assert len(apply_migrations(database))==3
    with database.connect() as connection:connection.execute('INSERT INTO llm_usage_totals(id) VALUES (1)')
    assert len(apply_migrations(database))==6
    with database.connect() as connection:
        assert connection.execute('SELECT id FROM llm_usage_totals').fetchall()==[{'id':1}]


@pytest.mark.parametrize(('baseline','fingerprint','location'),[
    (4,'d8d2181d194738d72ceb9029b7ec31ada66cac21b9157a49687ad9439ba1e5f2',None),
    (5,'10a49c5d5b0abba14d009aa7704f2dc6e59636396b1a74c29cc8f2978c107181','18600-2 / 船体 / Gen指令'),
])
def test_file_location_migration_preserves_existing_dispatches(postgres,source,monkeypatch,baseline,fingerprint,location):
    import hashlib
    import app.database.postgres_migrations as migrations
    from psycopg import sql
    database,_,_=postgres
    def original_schema(connection):
        digest=hashlib.sha256(json.dumps(migrations.catalog(connection),sort_keys=True).encode()).hexdigest()
        assert digest==fingerprint
        return connection.execute('SELECT version,checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
    with monkeypatch.context() as patch:
        patch.setattr(migrations,'VERSIONS',migrations.VERSIONS[:baseline])
        patch.setattr(migrations,'validate_schema',original_schema)
        assert len(apply_migrations(database))==baseline
    with sqlite3.connect(source) as original, database.connect() as target:
        original.row_factory=sqlite3.Row
        for table in ('users','cutting_dispatches'):
            row=dict(original.execute(f'SELECT * FROM {table} LIMIT 1').fetchone())
            if table=='cutting_dispatches' and baseline==5:row['file_location']=location
            target.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),
                sql.SQL(',').join(map(sql.Identifier,row)),sql.SQL(',').join(sql.Placeholder() for _ in row)),tuple(row.values()))
        before=target.execute('SELECT * FROM cutting_dispatches').fetchone()
    assert len(apply_migrations(database))==6
    with database.connect() as connection:
        assert connection.execute('SELECT * FROM cutting_dispatches').fetchone()=={**before,'file_location':location,'processing_finished_at':None,'processing_finish_by':None,'completion_received_at':None}


def test_ascii_nocase_partial_uniqueness_and_cascade_are_preserved(postgres,source):
    import psycopg
    database,_,_=postgres
    apply_migrations(database);perform(source,database)
    def insert_user(connection,key,username):
        connection.execute('''INSERT INTO users(user_id,username,display_name,password_hash,role,auth_source,created_at,updated_at)
            VALUES(%s,%s,'test','hash','worker','local','2026-11-04','2026-11-04')''',(key,username))
    with database.connect() as connection: insert_user(connection,'ascii','CaseUser')
    with pytest.raises(psycopg.errors.UniqueViolation):
        with database.connect() as connection: insert_user(connection,'ascii2','caseuser')
    with database.connect() as connection:
        insert_user(connection,'unicode1','Ä');insert_user(connection,'unicode2','ä')
        connection.execute("INSERT INTO auth_sessions VALUES('new-session','ascii','2026-11-04','2027-01-01')")
        connection.execute("DELETE FROM users WHERE user_id='ascii'")
        assert not connection.execute("SELECT 1 FROM auth_sessions WHERE session_hash='new-session'").fetchone()
    with pytest.raises(psycopg.errors.UniqueViolation):
        with database.connect() as connection:
            connection.execute("INSERT INTO users SELECT 'dupe', 'unique-name',display_name,password_hash,role,auth_source,external_id,group_no,company_no,active,created_at,updated_at FROM users WHERE external_id IS NOT NULL LIMIT 1")


def test_cli_schema_check_migrate_verify_use_real_results_and_hide_credentials(postgres_state,source):
    database,_,cfg=postgres_state
    root=Path(__file__).resolve().parents[2]
    env={**os.environ, 'POSTGRES_HOST':cfg.postgres_host,'POSTGRES_PORT':str(cfg.postgres_port),
         'POSTGRES_DATABASE':cfg.postgres_database,'POSTGRES_USER':cfg.postgres_user,'POSTGRES_PASSWORD':cfg.postgres_password}
    for action in ('schema','status','check','migrate','verify'):
        args=[sys.executable,str(root/'scripts/migrate_sqlite_to_postgres.py'),action]
        if action not in ('schema','status'):
            args += ['--sqlite',str(source),'--reports-root',str(source.parent/'reports'),'--data-root',str(source.parent/'data'),'--batch-size','2']
        result=subprocess.run(args,cwd=root,env=env,capture_output=True,text=True,timeout=20)
        assert result.returncode==0,result.stderr
        assert cfg.postgres_password not in result.stdout+result.stderr
        payload=json.loads(result.stdout)
        if action in ('migrate','verify'): assert payload['result']=='verified' and payload['total_rows']==46


def test_incompatible_text_is_rejected_without_printing_values(postgres,source):
    database,_,_=postgres
    apply_migrations(database)
    with sqlite3.connect(source) as connection:
        connection.execute('UPDATE users SET display_name=?',('private-value\x00must-not-print',))
    with pytest.raises(MigrationError,match='users.display_name') as error:
        perform(source,database)
    assert 'private-value' not in str(error.value)
    with database.connect() as connection:
        assert connection.execute('SELECT count(*) AS n FROM _sqlite_imports').fetchone()['n']==0


def test_schema_owner_without_database_create_can_migrate(postgres,source):
    from psycopg import sql
    database,cfg,_=postgres
    role='p4_migrator_'+uuid4().hex
    password='isolated-role-test-only'
    try:
        with database.connect() as admin:
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
            admin.execute(sql.SQL('CREATE SCHEMA app_state AUTHORIZATION {}').format(sql.Identifier(role)))
            assert not admin.execute('SELECT has_database_privilege(%s,%s,\'CREATE\') AS permitted',(role,cfg.postgres_database)).fetchone()['permitted']
        with PostgresDatabase(replace(cfg,postgres_user=role,postgres_password=password)) as migrator:
            apply_migrations(migrator)
            assert perform(source,migrator)[0]['result']=='verified'
    finally:
        with database.connect() as admin:
            admin.execute('DROP SCHEMA IF EXISTS app_state CASCADE')
            admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))
