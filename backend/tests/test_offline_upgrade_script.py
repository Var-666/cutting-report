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
    if 'python' in a and '-c' in a:
        code=a[a.index('-c')+1]
        if 'json.load(sys.stdin)' in code:
            r=__import__('subprocess').run([sys.executable,*a[a.index('python')+1:]],input=sys.stdin.read(),text=True)
            sys.exit(r.returncode)
    elif 'sh' in a and any('du -sk' in x for x in a):print('1')
''')
    docker.chmod(0o755)

    def run(**changes):
        if changes.pop('USE_ACTUAL_STARTER', False):
            shutil.copy(ROOT / 'scripts/linux/start_offline_docker.sh', starter)
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
        result = subprocess.run(command,
                                env={**env, **changes}, text=True, capture_output=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls
    return run, backups


def actions(calls):
    return [call[call.index('python')+2] for call in calls
            if call[0]=='compose' and 'scripts/manage_postgres.py' in call]


def test_pg_update_checks_current_schema_before_starting_roles(upgrade):
    run, _ = upgrade
    result, calls = run(USE_ACTUAL_STARTER=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions(calls) == ['schema', 'status']
    schema = next(i for i, call in enumerate(calls) if 'schema' in call)
    status = next(i for i, call in enumerate(calls) if 'status' in call)
    start = next(i for i, call in enumerate(calls) if '--force-recreate' in call)
    assert schema < status < start
    assert not any('prepare-consumers' in call for call in calls)


def test_pg_update_backs_up_then_appends_schema_without_reimporting(upgrade):
    run, backups = upgrade
    result, calls = run()
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
    result, calls = run(**{failure:'1'})
    assert result.returncode != 0
    assert not any('stop' in c for c in calls)
    assert not actions(calls)
    assert 'private-new-password' not in result.stdout + result.stderr


@pytest.mark.parametrize('failure', ['UPGRADE_TEST_FAIL_PG_BACKUP', 'UPGRADE_TEST_FAIL_ACTION'])
def test_pg_update_failed_backup_or_schema_does_not_start(upgrade, failure):
    run, _ = upgrade
    result, calls = run(**{failure:'schema' if failure.endswith('ACTION') else '1'})
    assert result.returncode != 0
    assert ['START_NEW'] not in calls
    assert not any('down' in c or 'prune' in c for c in calls)


def test_pg_update_failed_health_stops_new_roles_and_keeps_backup(upgrade):
    run, backups = upgrade
    result, calls = run(UPGRADE_TEST_FAIL_HEALTH='1')
    assert result.returncode != 0
    assert ['START_NEW'] in calls
    assert len([c for c in calls if 'stop' in c]) == 2
    assert list(backups.glob('cutting-before-update-*/postgres.dump'))
