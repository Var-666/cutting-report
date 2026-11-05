"""Offline P4 import: official SQLite backup, bounded copies and full content verification."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from time import time
from uuid import uuid4

from psycopg import sql
from sqlglot import parse_one, exp

from app.core.process_lock import FileLock
from app.database.migrations.registry import MIGRATIONS
from app.database.device_migrations import DEVICE_MIGRATION
from app.database.postgres_migrations import DIRECTORY, MIGRATION_LOCK, MigrationError, validate_schema, VERSIONS

SOURCE_SCHEMA = DIRECTORY / 'sqlite_schema.json'
LEGACY_SCHEMA = DIRECTORY / 'sqlite_legacy_schema.json'
ORDERED_TABLES = {'report_analyses', 'report_snapshots'}


def sqlite_schema(connection):
    result = {}
    objects = connection.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    if any(row['type'] not in ('table', 'index') for row in objects):
        raise MigrationError('SQLite 包含工具不支持的视图或触发器')
    for row in objects:
        if row['type'] != 'table': continue
        name = row['name']
        indexes = []
        for index in connection.execute(f'PRAGMA index_list("{name}")'):
            terms = [dict(term) for term in connection.execute(f'PRAGMA index_xinfo("{index["name"]}")') if term['key']]
            for term in terms: term.pop('cid'); term.pop('key')
            definition = next((o['sql'] for o in objects if o['name'] == index['name']), None)
            where = parse_one(definition, read='sqlite').this.args['params'].args.get('where') if definition else None
            indexes.append(dict(name=index['name'] if index['origin']=='c' else '', unique=index['unique'],
                                origin=index['origin'], terms=terms, where=where.sql(dialect='sqlite') if where else None))
        foreign = [dict(r) for r in connection.execute(f'PRAGMA foreign_key_list("{name}")')]
        for item in foreign: item.pop('id')
        result[name] = dict(columns={r['name']: {k:r[k] for k in ('type','notnull','dflt_value','pk')} for r in connection.execute(f'PRAGMA table_info("{name}")')},
            indexes=sorted(indexes,key=lambda x:json.dumps(x,sort_keys=True)),
            foreign_keys=sorted(foreign,key=lambda x:json.dumps(x,sort_keys=True)),
            checks=sorted(c.this.sql(dialect='sqlite') for c in parse_one(row['sql'],read='sqlite').find_all(exp.CheckColumnConstraint)))
    return result


@contextmanager
def offline_snapshot(path: Path, backup_dir: Path):
    """Keep known processes and every SQLite writer out until the caller finishes."""
    path = path.resolve()
    if not path.is_file(): raise MigrationError('SQLite 源文件不存在')
    with ExitStack() as stack:
        for lock_path in (path.parent / '.web-process.lock', path.with_suffix('.initialize.lock'), path.with_suffix('.migrations.lock')):
            lock = FileLock(lock_path)
            if not lock.acquire(blocking=False): raise MigrationError('请停止 Web/Worker 及其他迁移进程')
            stack.callback(lock.release)
        guard = sqlite3.connect(path, timeout=0)
        stack.callback(guard.close)
        try: guard.execute('BEGIN EXCLUSIVE')
        except sqlite3.OperationalError: raise MigrationError('SQLite 仍有写入进程，请停止后重试') from None
        try:
            if guard.execute("SELECT 1 FROM sqlite_master WHERE name='process_leases'").fetchone():
                if guard.execute('SELECT 1 FROM process_leases WHERE expires_at>?', (time(),)).fetchone():
                    raise MigrationError('SQLite 仍有有效进程租约，请停止 Worker 并等待释放')
            backup_dir.mkdir(parents=True, exist_ok=True)
            target = backup_dir / f'{path.stem}-p4-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.db'
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600); os.close(fd)
            # A second read connection sees the last committed WAL state while guard blocks writers.
            with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as original, sqlite3.connect(target) as backup:
                original.backup(backup)
            source = sqlite3.connect(target.as_uri()+'?mode=ro', uri=True)
            source.row_factory = sqlite3.Row
            try: yield source, target
            finally: source.close()
        finally: guard.rollback()


def check_source(source):
    if source.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
        raise MigrationError('SQLite 完整性检查失败')
    if source.execute('PRAGMA foreign_key_check').fetchone():
        raise MigrationError('SQLite 外键检查失败')
    profile = sqlite_schema(source)
    expected = json.loads(SOURCE_SCHEMA.read_text())
    for table, definition in json.loads(LEGACY_SCHEMA.read_text()).items():
        if table in profile:
            expected[table] = definition
    version = profile.get('report_analyses', {}).get('columns', {}).get('data_version')
    if version == {**expected['report_analyses']['columns']['data_version'], 'notnull': 0}:
        # The old ALTER TABLE added a nullable column, but populated its values.
        expected['report_analyses']['columns']['data_version']['notnull'] = 0
        if source.execute('SELECT 1 FROM report_analyses WHERE data_version IS NULL LIMIT 1').fetchone():
            raise MigrationError('report_analyses.data_version 存在真实空值，请先核对历史版本，禁止自动猜填')
    if profile != expected:
        raise MigrationError('SQLite Schema 与当前 001–016 基线不匹配；先用原升级工具更新，禁止遗漏表')
    versions = [dict(r) for r in source.execute('SELECT name,applied_at FROM schema_migrations ORDER BY name')]
    if [r['name'] for r in versions if r['name'] != DEVICE_MIGRATION] != [n for n,_ in MIGRATIONS]:
        raise MigrationError('SQLite 迁移历史不完整或超出当前支持版本')
    return profile, versions


def table_order(profile):
    pending = set(profile)-{'schema_migrations'}
    ordered = []
    while pending:
        ready = sorted(t for t in pending if {f['table'] for f in profile[t]['foreign_keys']} <= set(ordered))
        if not ready: raise MigrationError('SQLite 外键存在循环，需追加明确迁移支持')
        ordered.extend(ready); pending.difference_update(ready)
    return ordered


def normalize(value, kind, table, column):
    if value is None: return None
    if kind == 'INTEGER' and type(value) is int: return ['i', str(value)]
    if kind == 'REAL' and type(value) in (int,float): return ['f', float(value).hex()]
    if kind == 'TEXT' and isinstance(value,str) and '\x00' not in value: return ['s',value]
    if kind == 'BLOB' and isinstance(value,(bytes,memoryview)): return ['b',bytes(value).hex()]
    raise MigrationError(f'{table}.{column}: 字段类型不兼容（不输出字段值）')


def batches(connection, table, columns, profile, batch_size, *, postgres=False):
    primary = sorted((v['pk'],k) for k,v in profile['columns'].items() if v['pk'])
    if not primary: raise MigrationError(f'{table}: 缺少稳定主键')
    selected = [sql.SQL('rowid AS insertion_order') if not postgres and column == 'insertion_order'
                else sql.Identifier(column) for column in columns]
    query = sql.SQL('SELECT {} FROM {} ORDER BY {}').format(sql.SQL(',').join(selected),
        sql.Identifier(table), sql.SQL(',').join(sql.Identifier(k) for _,k in primary))
    cursor = connection.cursor(name='p4_'+uuid4().hex) if postgres else connection.cursor()
    try:
        cursor.execute(query if postgres else query.as_string())
        while rows := cursor.fetchmany(batch_size): yield rows
    finally: cursor.close()


def digest_table(connection, table, profile, batch_size, *, postgres=False):
    columns = sorted(profile['columns'])
    digest, count = sha256(), 0
    for rows in batches(connection,table,columns,profile,batch_size,postgres=postgres):
        for row in rows:
            values = []
            for col in columns:
                if profile['columns'][col]['pk'] and row[col] is None:
                    raise MigrationError(f'{table}: 主键含 NULL，不能转换成 PostgreSQL 主键')
                values.append(normalize(row[col],profile['columns'][col]['type'],table,col))
            data = json.dumps(values,ensure_ascii=False,separators=(',',':')).encode()
            digest.update(len(data).to_bytes(8,'big')); digest.update(data); count += 1
    return {'rows':count,'sha256':digest.hexdigest()}


def insertion_order_digest(connection, table, profile, batch_size, *, postgres=False):
    columns = sorted(name for name, meta in profile['columns'].items() if meta['pk']) + ['insertion_order']
    digest = sha256()
    for rows in batches(connection, table, columns, profile, batch_size, postgres=postgres):
        for row in rows:
            data = json.dumps([row[column] for column in columns], ensure_ascii=False, separators=(',', ':')).encode()
            digest.update(len(data).to_bytes(8, 'big'))
            digest.update(data)
    return digest.hexdigest()


# Preserve soft historical links as well as actual foreign keys; never repair or drop orphans.
RELATIONS = {
    'sessions_users': ('auth_sessions','user_id','users','user_id'),
    'reports_jobs': ('reports','report_id','report_jobs','report_id'),
    'runs_schedules': ('scheduled_report_runs','schedule_id','report_schedules','schedule_id'),
    'emails_runs': ('email_deliveries','run_id','scheduled_report_runs','run_id'),
    'emails_reports': ('email_deliveries','report_id','report_jobs','report_id'),
    'alarm_events_incidents': ('alarm_source_events','incident_id','alarm_incidents','incident_id'),
    'alarm_incidents_reports': ('alarm_incidents','pdf_report_id','alarm_reports','report_id'),
    'confirmations_workers': ('workshop_confirmations','worker_id','users','user_id'),
    'confirmations_reports': ('workshop_confirmations','report_id','workshop_reports','report_id'),
    'items_reports': ('workshop_report_items','report_id','workshop_reports','report_id'),
}


def relations(connection):
    result = {}
    for name,(child,fk,parent,pk) in RELATIONS.items():
        row = connection.execute(f'''SELECT COUNT(*) AS referenced,
            COUNT(p."{pk}") AS matched FROM "{child}" c LEFT JOIN "{parent}" p ON c."{fk}"=p."{pk}" WHERE c."{fk}" IS NOT NULL''').fetchone()
        result[name] = dict(row)
    # These have no FK: full per-table hashes preserve payload, scope/source keys and cadence exactly.
    for table, columns in [('background_tasks',('device_id','dedup_key')), ('background_schedules',('schedule_key',)),
                           ('report_snapshots',('line_id','source_fingerprint','query_fingerprint'))]:
        result[table+'_identities'] = {col: connection.execute(f'SELECT COUNT(DISTINCT "{col}") AS count FROM "{table}"').fetchone()['count'] for col in columns}
    return result


def check_files(source, reports_root, data_root):
    checked = missing = removed = 0
    def check(root, relative):
        nonlocal checked,missing
        if not relative: return
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()): raise MigrationError('数据库文件引用越出指定根目录')
        checked += 1
        if not path.is_file(): missing += 1
    for row in source.execute('SELECT relative_path FROM reports'): check(reports_root,row['relative_path'])
    for row in source.execute('SELECT html_relative_path,pdf_relative_path FROM alarm_reports'):
        for value in row: check(reports_root,value)
    for row in source.execute('SELECT payload FROM cutting_downloads'):
        payload = json.loads(row['payload'])
        if payload.get('file_deleted_at'): removed += 1; continue
        if payload.get('status') == 'downloaded':
            check(data_root / 'cutting-downloads', str(payload['download_id'])+'/'+str(payload['filename']))
    if missing: raise MigrationError(f'外部实体文件检查失败：检查 {checked} 个引用，缺失 {missing} 个；请提供同批文件备份')
    return {'checked':checked,'missing':missing,'retained_deleted_downloads':removed}


def transfer(source, database, *, action, batch_size=1000, reports_root, data_root):
    if not 1 <= batch_size <= 10000: raise MigrationError('batch_size 必须在 1 到 10000 之间')
    profile, versions = check_source(source)
    tables = table_order(profile)
    source_tables = {name:digest_table(source,name,profile[name],batch_size) for name in tables}
    for name in ORDERED_TABLES:
        source_tables[name]['insertion_order_sha256'] = insertion_order_digest(source, name, profile[name], batch_size)
    source_relations = relations(source)
    files = check_files(source,reports_root,data_root)
    identity = sha256(json.dumps([source_tables,versions],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with database.connect() as target:
        target.execute('SELECT pg_advisory_xact_lock(%s)',(MIGRATION_LOCK,))
        validate_schema(target)
        # ACCESS EXCLUSIVE also excludes any future noncooperating P5 writer during verification.
        absent_legacy = set(json.loads(LEGACY_SCHEMA.read_text())) - set(tables)
        target.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.SQL(',').join(sql.Identifier(t) for t in tables+sorted(absent_legacy)+['_sqlite_imports','_sqlite_schema_versions'])))
        for table in absent_legacy:
            if target.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(table))).fetchone():
                raise MigrationError(f'{table}: 目标有源库不存在的历史记录，拒绝遗漏')
        prior = target.execute('SELECT source_digest FROM _sqlite_imports').fetchall()
        if prior and prior != [{'source_digest':identity}]: raise MigrationError('目标来自不同源快照，拒绝覆盖；请使用新的空目标库')
        if not prior:
            if action=='verify': raise MigrationError('目标没有已完成的迁移记录')
            for table in tables+['_sqlite_schema_versions']:
                if target.execute(sql.SQL('SELECT 1 FROM {} LIMIT 1').format(sql.Identifier(table))).fetchone():
                    raise MigrationError(f'{table}: 目标存在来源不明的数据，拒绝覆盖')
            if action=='check': return dict(source_schema=MIGRATIONS[-1][0],postgres_schema=VERSIONS[-1],tables=source_tables,relations=source_relations,files=files,result='ready')
            for table in tables:
                columns = sorted(profile[table]['columns']) + (['insertion_order'] if table in ORDERED_TABLES else [])
                statement = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                try:
                    with target.cursor() as cursor:
                        for rows in batches(source,table,columns,profile[table],batch_size):
                            cursor.executemany(statement,[tuple(row[c] for c in columns) for row in rows])
                except Exception as exc:
                    raise MigrationError(f'{table}: 导入失败（{type(exc).__name__}），事务已回滚') from None
        for table in tables:
            actual = digest_table(target,table,profile[table],batch_size,postgres=True)
            if table in ORDERED_TABLES:
                actual['insertion_order_sha256'] = insertion_order_digest(target, table, profile[table], batch_size, postgres=True)
            if actual != source_tables[table]:
                raise MigrationError(f'{table}: 行数、主键或字段摘要不一致')
        if relations(target) != source_relations: raise MigrationError('业务关联校验不一致')
        summary = dict(source_schema=MIGRATIONS[-1][0],postgres_schema=VERSIONS[-1],tables=source_tables,relations=source_relations,files=files,
                       table_count=len(tables),total_rows=sum(t['rows'] for t in source_tables.values()),result='verified')
        if prior:
            if target.execute('SELECT name,applied_at FROM _sqlite_schema_versions ORDER BY name').fetchall() != versions:
                raise MigrationError('源 SQLite 迁移历史记录不一致')
            recorded = target.execute('SELECT source_schema,summary_json FROM _sqlite_imports').fetchone()
            if recorded['source_schema'] != MIGRATIONS[-1][0] or json.loads(recorded['summary_json']) != summary:
                raise MigrationError('已完成迁移的校验摘要被修改')
        else:
            next_id = target.execute('SELECT COALESCE(MAX(id),0)+1 AS value FROM llm_usage_calls').fetchone()['value']
            target.execute(sql.SQL('ALTER SEQUENCE llm_usage_calls_id_seq RESTART WITH {}').format(sql.Literal(next_id)))
            for table in ORDERED_TABLES:
                next_order = target.execute(sql.SQL('SELECT COALESCE(MAX(insertion_order),0)+1 AS value FROM {}').format(sql.Identifier(table))).fetchone()['value']
                target.execute(sql.SQL('ALTER SEQUENCE {} RESTART WITH {}').format(sql.Identifier(table+'_insertion_order_seq'), sql.Literal(next_order)))
            target.execute('INSERT INTO _sqlite_imports VALUES (%s,%s,%s,%s)',(identity,MIGRATIONS[-1][0],datetime.now(timezone.utc).isoformat(),json.dumps(summary,ensure_ascii=False)))
            with target.cursor() as cursor:
                cursor.executemany('INSERT INTO _sqlite_schema_versions VALUES (%s,%s,%s)',[(identity,r['name'],r['applied_at']) for r in versions])
        return summary
