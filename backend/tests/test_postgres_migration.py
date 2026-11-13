"""Real isolated PostgreSQL integration checks, opt in with P4_TEST_POSTGRES=1."""
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4
import multiprocessing as mp

import pytest

from app.database.postgres import PostgresDatabase
from app.database.postgres_migrations import apply_migrations, validate_schema, MigrationError, DIRECTORY
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
        expected=json.loads((DIRECTORY/'schema.json').read_text())
        assert len(tables)==expected['tables']
        assert {'users','cutting_dispatches','workshop_reports','_postgres_schema_migrations'} <= tables
        assert connection.execute("SELECT count(*) AS n FROM pg_indexes WHERE schemaname='app_state'").fetchone()['n']==expected['indexes']
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
    assert len(apply_migrations(database))==len(migrations.VERSIONS)
    with database.connect() as connection:
        assert connection.execute('SELECT id FROM llm_usage_totals').fetchall()==[{'id':1}]


def test_monitor_migration_preserves_existing_007_state(postgres, monkeypatch):
    import hashlib
    import app.database.postgres_migrations as migrations
    database, _, _ = postgres
    def old_schema(connection):
        digest = hashlib.sha256(json.dumps(migrations.catalog(connection), sort_keys=True).encode()).hexdigest()
        assert digest == '822f84c9bcf72b534b8589ef5faef6e3be040d9c01d297a2f7b5ff11a4e2ef96'
        return connection.execute('SELECT version,checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'VERSIONS', migrations.VERSIONS[:7])
        patch.setattr(migrations, 'validate_schema', old_schema)
        assert len(apply_migrations(database)) == 7
    with database.connect() as connection:
        connection.execute('INSERT INTO llm_usage_totals(id) VALUES (1)')
    assert len(apply_migrations(database)) == len(migrations.VERSIONS)
    with database.connect() as connection:
        assert connection.execute('SELECT id FROM llm_usage_totals').fetchall() == [{'id': 1}]
        assert connection.execute('SELECT * FROM database_monitors').fetchall() == []


def test_analysis_delivery_migration_preserves_008_jobs_and_success_cache(postgres, monkeypatch):
    import hashlib
    import app.database.postgres_migrations as migrations
    database, _, _ = postgres
    def old_schema(connection):
        digest = hashlib.sha256(json.dumps(migrations.catalog(connection), sort_keys=True).encode()).hexdigest()
        assert digest == '37cc8d656bdc1fd49a122bd77bfa7e7baa6086b8554eea229a20afb9abb267a0'
        return connection.execute('SELECT version,checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
    with monkeypatch.context() as patch:
        patch.setattr(migrations, 'VERSIONS', migrations.VERSIONS[:8])
        patch.setattr(migrations, 'validate_schema', old_schema)
        assert len(apply_migrations(database)) == 8
    with database.connect() as connection:
        connection.execute("""INSERT INTO report_analyses(cache_key,report_type,report_date,input_hash,data_version,
            status,mode,prompt_version,result_json,generated_at) VALUES ('old','workshop','2026-11-12','hash','version',
            'success','model','old-prompt','{}','2026-11-12T09:55:00+08:00')""")
        connection.execute("""INSERT INTO report_jobs(report_id,device_id,status,stage_message,report_date,created_at,request_id)
            VALUES ('waiting','Cutter01','pending','等待生成','2026-11-12','2026-11-12T10:00:00+08:00','request')""")
    assert len(apply_migrations(database)) == len(migrations.VERSIONS)
    with database.connect() as connection:
        row = connection.execute("SELECT * FROM report_analyses WHERE cache_key='old'").fetchone()
        assert row['generated_at'] == '2026-11-12T09:55:00+08:00' and row['reuse_scope'] is None and row['reuse_key'] is None
        row = connection.execute("SELECT * FROM report_jobs WHERE report_id='waiting'").fetchone()
        assert row['status'] == 'pending' and row['analysis_attempts'] == 0 and row['analysis_next_attempt'] == 0


@pytest.mark.parametrize(('baseline','fingerprint','location'),[
    (4,'d8d2181d194738d72ceb9029b7ec31ada66cac21b9157a49687ad9439ba1e5f2',None),
    (5,'10a49c5d5b0abba14d009aa7704f2dc6e59636396b1a74c29cc8f2978c107181','18600-2 / 船体 / Gen指令'),
    (6,'9973144f7c70422993ec68bd47a1ae1eab84b2944a8e2434867f846c22ea9440',None),
])
def test_file_location_migration_preserves_existing_dispatches(postgres,monkeypatch,baseline,fingerprint,location):
    import hashlib
    import app.database.postgres_migrations as migrations
    database,_,_=postgres
    def original_schema(connection):
        digest=hashlib.sha256(json.dumps(migrations.catalog(connection),sort_keys=True).encode()).hexdigest()
        assert digest==fingerprint
        return connection.execute('SELECT version,checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
    with monkeypatch.context() as patch:
        patch.setattr(migrations,'VERSIONS',migrations.VERSIONS[:baseline])
        patch.setattr(migrations,'validate_schema',original_schema)
        assert len(apply_migrations(database))==baseline
    with database.connect() as target:
        target.execute("""INSERT INTO users(user_id,username,display_name,password_hash,role,created_at,updated_at)
            VALUES ('creator','creator','测试','hash','worker','2026-11-04','2026-11-04')""")
        target.execute("""INSERT INTO cutting_dispatches(dispatch_id,document_id,project_id,document_no,relative_path,
            machine_ip,quantity,status,source_mode,created_by_id,created_by_name,created_at,updated_at)
            VALUES ('dispatch','doc','project','test.gen','test.gen','10.0.0.1',1,'sent','sqlserver',
                'creator','测试','2026-11-04','2026-11-04')""")
        if baseline==5:
            target.execute('UPDATE cutting_dispatches SET file_location=%s',(location,))
        before=target.execute('SELECT * FROM cutting_dispatches').fetchone()
    assert len(apply_migrations(database))==len(migrations.VERSIONS)
    with database.connect() as connection:
        assert connection.execute('SELECT * FROM cutting_dispatches').fetchone()=={**before,'file_location':location,'processing_finished_at':None,'processing_finish_by':None,'completion_received_at':None}


def test_ascii_nocase_partial_uniqueness_and_cascade_are_preserved(postgres):
    import psycopg
    database,_,_=postgres
    apply_migrations(database)
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
    with database.connect() as connection:
        insert_user(connection,'external','external')
        connection.execute("UPDATE users SET external_id='ext-001' WHERE user_id='external'")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with database.connect() as connection:
            connection.execute("INSERT INTO users SELECT 'dupe', 'unique-name',display_name,password_hash,role,auth_source,external_id,group_no,company_no,active,created_at,updated_at FROM users WHERE external_id IS NOT NULL LIMIT 1")


def test_schema_owner_without_database_create_can_migrate(postgres):
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
            with migrator.connect() as connection:
                assert validate_schema(connection)
    finally:
        with database.connect() as admin:
            admin.execute('DROP SCHEMA IF EXISTS app_state CASCADE')
            admin.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(role)))
