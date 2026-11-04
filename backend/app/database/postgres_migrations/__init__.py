"""Explicit, transactional PostgreSQL migrations, separate from SQLite history."""
from hashlib import sha256
import json
from pathlib import Path

from psycopg import Connection

DIRECTORY = Path(__file__).parent
VERSIONS = ('001_initial', '002_runtime')
# Shared by schema installation and offline imports; released by transaction/OS exit.
MIGRATION_LOCK = 740314001


class MigrationError(RuntimeError):
    pass


def catalog(connection: Connection) -> dict:
    """Structural fingerprint, independent of rows, grants and sequence counters."""
    queries = {
        'columns': '''SELECT c.table_name,c.column_name,c.data_type,c.is_nullable,c.column_default,c.is_identity,c.identity_generation,c.collation_name
            FROM information_schema.columns c WHERE c.table_schema='app_state' ORDER BY c.table_name,c.ordinal_position''',
        'constraints': '''SELECT r.relname AS table_name,c.conname,c.contype,pg_get_constraintdef(c.oid) AS definition,c.convalidated
            FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
            WHERE n.nspname='app_state' ORDER BY r.relname,c.conname''',
        'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='app_state' ORDER BY tablename,indexname",
        'functions': '''SELECT p.proname,pg_get_functiondef(p.oid) AS definition FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace=n.oid WHERE n.nspname='app_state' ORDER BY p.proname''',
        'relations': '''SELECT c.relname,c.relkind,c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='app_state' AND c.relkind IN ('r','v','m','S','f') ORDER BY c.relname''',
        'triggers': '''SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) AS definition FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='app_state' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname''',
    }
    return {key: connection.execute(query).fetchall() for key, query in queries.items()}


def validate_schema(connection: Connection) -> list[dict]:
    if connection.info.server_version // 10000 != 18:
        raise MigrationError('当前 PostgreSQL 基线仅支持已验证的18主版本')
    if connection.execute("SELECT 1 FROM pg_tables WHERE schemaname NOT IN ('app_state','information_schema') AND schemaname NOT LIKE 'pg_%' LIMIT 1").fetchone():
        raise MigrationError('目标数据库包含 app_state 之外的未知业务表，请使用独立空目标库')
    records = connection.execute('SELECT version, checksum FROM _postgres_schema_migrations ORDER BY version').fetchall()
    expected = [{'version': name, 'checksum': sha256((DIRECTORY / f'{name}.sql').read_bytes()).hexdigest()} for name in VERSIONS]
    if records != expected:
        raise MigrationError('PostgreSQL Schema 版本或已发布脚本校验和不匹配')
    digest = sha256(json.dumps(catalog(connection),sort_keys=True).encode()).hexdigest()
    if digest != json.loads((DIRECTORY / 'schema.json').read_text())['sha256']:
        raise MigrationError('PostgreSQL Schema 结构漂移，拒绝迁移数据')
    return records


def apply_migrations(database) -> list[dict]:
    with database.connect() as connection:
        connection.execute('SELECT pg_advisory_xact_lock(%s)', (MIGRATION_LOCK,))
        if connection.execute("SELECT 1 FROM pg_tables WHERE schemaname NOT IN ('app_state','information_schema') AND schemaname NOT LIKE 'pg_%' LIMIT 1").fetchone():
            raise MigrationError('目标数据库包含其他业务表，拒绝初始化')
        # Even IF NOT EXISTS requires database CREATE privilege on PostgreSQL.
        if not connection.execute("SELECT 1 FROM pg_namespace WHERE nspname='app_state'").fetchone():
            connection.execute('CREATE SCHEMA app_state')
        present = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='app_state'").fetchall()
        if present and not any(row['tablename'] == '_postgres_schema_migrations' for row in present):
            raise MigrationError('目标 Schema 包含来源不明的表，拒绝初始化')
        connection.execute('''CREATE TABLE IF NOT EXISTS _postgres_schema_migrations (
            version text PRIMARY KEY, checksum text NOT NULL, applied_at text NOT NULL)''')
        applied = {r['version']: r['checksum'] for r in connection.execute('SELECT version,checksum FROM _postgres_schema_migrations')}
        if set(applied) - set(VERSIONS):
            raise MigrationError('目标 PostgreSQL Schema 高于当前工具版本')
        for version in VERSIONS:
            script = (DIRECTORY / f'{version}.sql').read_bytes()
            checksum = sha256(script).hexdigest()
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationError('已发布的 PostgreSQL 迁移脚本被修改')
                continue
            connection.execute(script.decode(), prepare=False)
            connection.execute('INSERT INTO _postgres_schema_migrations VALUES (%s,%s,CURRENT_TIMESTAMP::text)', (version, checksum))
        return validate_schema(connection)
