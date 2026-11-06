"""Exercise the maintenance shell flow with a Docker CLI stand-in, never production."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def upgrade(tmp_path):
    new = tmp_path / 'new package'
    old = tmp_path / 'old package'
    backups = tmp_path / 'backups'
    binary = tmp_path / 'bin'
    for folder in (new / 'scripts/linux', new / 'images', old, backups, binary):
        folder.mkdir(parents=True, exist_ok=True)
    for folder in (new, old):
        (folder / '.env').write_text('PRIVATE=test-only\n')
        (folder / 'compose.yaml').write_text('name: cutting-report\n')
        (folder / 'compose.offline.yaml').write_text('services: {}\n')
    (new / 'SHA256SUMS').write_text('fixture\n')
    (new / 'images/runtime.tar.gz').write_bytes(b'archive stand-in')
    script = new / 'scripts/linux/upgrade_offline_docker.sh'
    shutil.copy(ROOT / 'scripts/linux/upgrade_offline_docker.sh', script)
    resume_script = new / 'scripts/linux/resume_sqlite_upgrade.sh'
    shutil.copy(ROOT / 'scripts/linux/resume_sqlite_upgrade.sh', resume_script)
    update_script = new / 'scripts/linux/update_offline_docker.sh'
    shutil.copy(ROOT / 'scripts/linux/update_offline_docker.sh', update_script)
    starter = new / 'scripts/linux/start_offline_docker.sh'
    starter.write_text('#!/bin/sh\nprintf \'["START_NEW"]\\n\' >> "$UPGRADE_TEST_LOG"\n')
    starter.chmod(0o755)
    log = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
           'UPGRADE_TEST_LOG': str(log), 'UPGRADE_TEST_OLD': str(old),
           'UPGRADE_TEST_NEW': str(new),
           'UPGRADE_TEST_ROOT': str(tmp_path)}
    for command, source in {
        'uname': '#!/bin/sh\nif [ "$1" = -s ]; then echo Linux; else echo x86_64; fi\n',
        'id': '#!/bin/sh\necho 0\n',
        'flock': '#!/bin/sh\nexit 0\n',
        'sha256sum': '#!/bin/sh\nexit "${UPGRADE_TEST_CHECKSUM_FAILURE:-0}"\n',
    }.items():
        path = binary / command
        path.write_text(source)
        path.chmod(0o755)
    docker = binary / 'docker'
    docker.write_text(f'#!{sys.executable}\n' + r'''
import json,os,sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['UPGRADE_TEST_LOG'],'a') as f:f.write(json.dumps(a)+'\n')
if a[0]=='info':
    if '--format' in a:print(os.environ['UPGRADE_TEST_ROOT'])
elif a[0]=='inspect':
    fmt=a[a.index('--format')+1] if '--format' in a else ''
    if 'compose.service' in fmt:print('app' if a[-1]=='old-app' else 'worker')
    elif 'compose.project' in fmt:print('cutting-report')
    elif '.Image' in fmt:print('sha256:old-image')
    elif '.Mounts' in fmt:
        print('cutting-report_postgres_data' if '/var/lib/postgresql' in fmt else 'cutting-report_cutting_reports' if '/app/reports' in fmt else 'cutting-report_cutting_logs' if '/app/logs' in fmt else 'another-volume' if os.environ.get('UPGRADE_TEST_UPDATE_WRONG_VOLUME') else 'cutting-report_cutting_data')
    else:print('[]')
elif a[:2]==['volume','inspect']:
    if a[-1]=='cutting-report_postgres_data':sys.exit(0 if os.environ.get('UPGRADE_TEST_PG_EXISTS') else 1)
elif a[0]=='compose':
    if 'config' in a:
        if '--images' in a:print('cutting-report:local\npostgres:18.6-bookworm')
        else:
            vols={k:{'name':'cutting-report_'+k} for k in ('cutting_data','cutting_reports','cutting_logs','postgres_data')}
            if os.environ.get('UPGRADE_TEST_WRONG_VOLUME'):vols['cutting_data']['name']='another-empty-volume'
            print(json.dumps({'name':'cutting-report','volumes':vols}))
    elif 'ps' in a and '-q' in a:
        if 'postgres' in a:print('old-postgres')
        elif 'app' in a and len(a)>a.index('ps')+1 and a[-1]=='app':print('old-app')
        elif '--status' not in a or os.environ.get('UPGRADE_TEST_ROLES_RUNNING'):print('old-app\nold-worker')
    elif 'exec' in a and any('pg_dump' in value for value in a):
        if os.environ.get('UPGRADE_TEST_FAIL_PG_BACKUP'):sys.exit(27)
        print('PGDMP-test-archive')
    elif 'exec' in a and os.environ.get('UPGRADE_TEST_FAIL_HEALTH'):sys.exit(23)
    elif 'run' in a:
        action=next((x for x in ('schema','check','migrate','verify','prepare-consumers') if x in a[a.index('python')+1:]),'upgrade')
        if os.environ.get('UPGRADE_TEST_FAIL_ACTION')==action:sys.exit(19)
        print(json.dumps({'result': 'ready' if action=='check' else 'verified','tables':{'fixture':{}}}))
elif a[0]=='run':
    if any('/release:ro' in x for x in a):
        code=sys.stdin.read().replace("Path('/release')",'Path('+repr(os.environ['UPGRADE_TEST_NEW'])+')')
        code=code.replace("'/old.env'",repr(str(Path(os.environ['UPGRADE_TEST_OLD'])/'.env')))
        r=__import__('subprocess').run([sys.executable,'-'],input=code,text=True)
        sys.exit(r.returncode)
    if any('/backup/app-files.tgz' in x for x in a):
        mount=next(x for x in a if x.endswith(':/backup:z'))
        (Path(mount[:-len(':/backup:z')])/'app-files.tgz').write_bytes(b'archive-stand-in')
    if 'python' in a and a[-1]=='-' and os.environ.get('UPGRADE_TEST_REAL_LEASE_VOLUME'):
        forwarded=[x.replace('cutting-report_cutting_data:',os.environ['UPGRADE_TEST_REAL_LEASE_VOLUME']+':')
                   if x.startswith('cutting-report_cutting_data:') else x for x in a]
        forwarded=[os.environ['UPGRADE_TEST_REAL_IMAGE'] if x=='cutting-report:local' else x for x in forwarded]
        r=__import__('subprocess').run([os.environ['UPGRADE_TEST_REAL_DOCKER'],*forwarded],input=sys.stdin.buffer.read())
        sys.exit(r.returncode)
    if 'python' in a and '-c' in a:
        code=a[a.index('-c')+1]
        if 'json.load(sys.stdin)' in code:
            r=__import__('subprocess').run([sys.executable,*a[a.index('python')+1:]],input=sys.stdin.read(),text=True)
            sys.exit(r.returncode)
    elif 'sh' in a and any('du -sk' in x for x in a):print('1')
''')
    docker.chmod(0o755)

    def run(*, resume=False, update=False, **changes):
        if update:
            pg = '\n'.join(f'{key}=test-only' for key in (
                'POSTGRES_HOST','POSTGRES_PORT','POSTGRES_DATABASE','POSTGRES_USER',
                'POSTGRES_PASSWORD','POSTGRES_BOOTSTRAP_PASSWORD','POSTGRES_MIGRATOR_PASSWORD'))+'\n'
            for folder in (new,old):
                (folder/'.env').write_text(pg)
            if changes.get('UPGRADE_TEST_ENV_PASSWORD_CHANGED'):
                (new/'.env').write_text(pg.replace('POSTGRES_PASSWORD=test-only','POSTGRES_PASSWORD=private-new-password'))
            files={p.relative_to(new).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in new.rglob('*') if p.is_file()}
            (new/'PACKAGE-MANIFEST.json').write_text(json.dumps({'files':files}))
            if changes.get('UPGRADE_TEST_PACKAGE_DRIFT'):
                (new/'compose.yaml').write_text('tampered')
            command=['bash',str(update_script),str(old),str(backups)]
        elif resume:
            prior = backups / 'prior'
            prior.mkdir()
            (prior / 'app-files.tgz').write_bytes(b'completed-backup-fixture')
            command = ['bash', str(resume_script), str(prior)]
        else:
            command = ['bash', str(script), str(old), str(backups)]
        result = subprocess.run(command,
                                env={**env, **changes}, text=True, capture_output=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return run, backups


def actions(calls):
    return [call[call.index('python')+2] for call in calls
            if call[0]=='compose' and 'scripts/migrate_sqlite_to_postgres.py' in call]


def test_upgrade_orders_verification_before_consumer_preparation_and_start(upgrade):
    run, backups = upgrade
    result, calls = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions(calls) == ['schema', 'check', 'migrate', 'verify', 'prepare-consumers']
    start = calls.index(['START_NEW'])
    assert next(i for i,c in enumerate(calls) if 'prepare-consumers' in c) < start
    assert all(c[0] != 'system' and 'down' not in c and 'prune' not in c for c in calls)
    assert 'new package' in str(calls)
    saved, = backups.glob('cutting-before-upgrade-*')
    assert saved.stat().st_mode & 0o777 == 0o700
    assert (saved / 'env.before').read_text() == 'PRIVATE=test-only\n'


@pytest.mark.parametrize('failure', ['UPGRADE_TEST_WRONG_VOLUME', 'UPGRADE_TEST_PG_EXISTS', 'UPGRADE_TEST_CHECKSUM_FAILURE'])
def test_preflight_refuses_uncertain_target_before_stopping_services(upgrade, failure):
    run, _ = upgrade
    result, calls = run(**{failure:'1'})
    assert result.returncode != 0
    assert not any('stop' in call for call in calls)
    assert ['START_NEW'] not in calls
    assert 'PRIVATE=test-only' not in result.stdout + result.stderr


def test_verify_failure_keeps_old_services_stopped_without_start_or_cleanup(upgrade):
    run, backups = upgrade
    result, calls = run(UPGRADE_TEST_FAIL_ACTION='verify')
    assert result.returncode != 0
    assert any('stop' in call for call in calls)
    assert 'prepare-consumers' not in actions(calls)
    assert ['START_NEW'] not in calls
    assert not any('down' in call or 'prune' in call or 'rm' in call for call in calls)
    assert list(backups.glob('cutting-before-upgrade-*/migrate.json'))
    assert '备份与维护记录' in result.stderr


def test_unhealthy_new_roles_are_stopped_without_restoring_old_sqlite(upgrade):
    run, _ = upgrade
    result, calls = run(UPGRADE_TEST_FAIL_HEALTH='1')
    assert result.returncode != 0
    assert ['START_NEW'] in calls
    stops = [call for call in calls if 'stop' in call]
    assert len(stops) == 2
    assert stops[-1][-4:] == ['app', 'scheduler', 'worker', 'automation']
    assert '不会自动清库或恢复旧版' in result.stderr


def test_resume_uses_completed_backup_and_verifies_before_start(upgrade):
    run, _ = upgrade
    result,calls = run(resume=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert actions(calls)==['schema','check','migrate','verify','prepare-consumers']
    assert ['START_NEW'] in calls
    assert not any(call[0]=='load' or 'scripts/migrate_database.py' in call for call in calls)


def test_resume_failed_check_does_not_import_or_start(upgrade):
    run, _ = upgrade
    result,calls=run(resume=True,UPGRADE_TEST_FAIL_ACTION='check')
    assert result.returncode!=0
    assert actions(calls)==['schema','check']
    assert ['START_NEW'] not in calls


def test_resume_refuses_already_running_application_before_maintenance(upgrade):
    run, _ = upgrade
    result,calls=run(resume=True,UPGRADE_TEST_ROLES_RUNNING='1')
    assert result.returncode!=0
    assert not actions(calls)
    assert not any('stop' in call for call in calls)


def test_pg_update_backs_up_then_appends_schema_without_reimporting(upgrade):
    run, backups = upgrade
    result, calls = run(update=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions(calls) == ['schema']
    stop = next(i for i,c in enumerate(calls) if 'stop' in c)
    dump = next(i for i,c in enumerate(calls) if any('pg_dump' in x for x in c))
    archive = next(i for i,c in enumerate(calls) if any('tar -czf' in x for x in c))
    schema = next(i for i,c in enumerate(calls) if 'schema' in c)
    assert stop < dump < archive < schema < calls.index(['START_NEW'])
    saved, = backups.glob('cutting-before-update-*')
    assert (saved/'postgres.dump').read_bytes().startswith(b'PGDMP')
    assert (saved/'app-files.tgz').stat().st_size > 0
    assert not any(c[0]=='load' or 'down' in c or 'prune' in c for c in calls)


@pytest.mark.parametrize('failure', ['UPGRADE_TEST_ENV_PASSWORD_CHANGED', 'UPGRADE_TEST_PACKAGE_DRIFT', 'UPGRADE_TEST_UPDATE_WRONG_VOLUME'])
def test_pg_update_refuses_unsafe_inputs_before_stopping(upgrade, failure):
    run, _ = upgrade
    result, calls = run(update=True, **{failure:'1'})
    assert result.returncode != 0
    assert not any('stop' in c for c in calls)
    assert not actions(calls)
    assert 'private-new-password' not in result.stdout + result.stderr


@pytest.mark.parametrize('failure', ['UPGRADE_TEST_FAIL_PG_BACKUP', 'UPGRADE_TEST_FAIL_ACTION'])
def test_pg_update_failed_backup_or_schema_does_not_start(upgrade, failure):
    run, _ = upgrade
    result, calls = run(update=True, **{failure:'schema' if failure.endswith('ACTION') else '1'})
    assert result.returncode != 0
    assert ['START_NEW'] not in calls
    assert not any('down' in c or 'prune' in c for c in calls)


def test_pg_update_failed_health_stops_new_roles_and_keeps_backup(upgrade):
    run, backups = upgrade
    result, calls = run(update=True, UPGRADE_TEST_FAIL_HEALTH='1')
    assert result.returncode != 0
    assert ['START_NEW'] in calls
    assert len([c for c in calls if 'stop' in c]) == 2
    assert list(backups.glob('cutting-before-update-*/postgres.dump'))


@pytest.mark.skipif(os.environ.get('OFFLINE_UPGRADE_DOCKER_TEST')!='1', reason='requires isolated Docker regression')
def test_wal_lease_read_uses_writable_auxiliary_files_without_changing_rows(upgrade):
    from uuid import uuid4
    docker = shutil.which('docker')
    assert docker
    image = 'cutting-report:runtime-20261104-v1'
    volume = 'cutting-upgrade-wal-test-' + uuid4().hex
    subprocess.run([docker,'volume','create',volume],check=True,capture_output=True)
    def command(code, *, readonly=False, root=False):
        args = [docker,'run','--rm','--network','none']
        if root:args += ['--user','0:0']
        args += ['-v',volume+':/app/data'+(':ro' if readonly else ''),image,'python','-c',code]
        return subprocess.run(args,text=True,capture_output=True)
    try:
        assert command("import os; os.chown('/app/data',10001,10001)",root=True).returncode==0
        seeded = command('''import sqlite3
from pathlib import Path
c=sqlite3.connect('/app/data/metadata.db')
assert c.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
c.execute('CREATE TABLE process_leases(name TEXT PRIMARY KEY, expires_at REAL)')
c.execute("INSERT INTO process_leases VALUES ('worker',1)")
c.commit();c.close()
assert not Path('/app/data/metadata.db-shm').exists()
''')
        assert seeded.returncode==0,seeded.stderr
        query="import sqlite3; c=sqlite3.connect('file:/app/data/metadata.db?mode=ro',uri=True); print(c.execute('SELECT * FROM process_leases').fetchall())"
        broken = command(query,readonly=True)
        assert broken.returncode!=0 and 'unable to open database file' in broken.stderr
        run, _ = upgrade
        result, _ = run(UPGRADE_TEST_REAL_LEASE_VOLUME=volume,UPGRADE_TEST_REAL_IMAGE=image,UPGRADE_TEST_REAL_DOCKER=docker)
        assert result.returncode==0,result.stdout+result.stderr
        checked = command('''import sqlite3
from pathlib import Path
c=sqlite3.connect('file:/app/data/metadata.db?mode=ro',uri=True)
assert c.execute('SELECT * FROM process_leases').fetchall()==[('worker',1.0)]
try:c.execute("UPDATE process_leases SET expires_at=2")
except sqlite3.OperationalError:pass
else:raise AssertionError('lease inspection must remain read-only')
for suffix in ('-wal','-shm'):
    p=Path('/app/data/metadata.db'+suffix)
    assert p.exists() and p.stat().st_uid==10001
''')
        assert checked.returncode==0,checked.stderr
    finally:
        subprocess.run([docker,'volume','rm',volume],check=True,capture_output=True)
