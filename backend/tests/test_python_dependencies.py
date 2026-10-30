"""Locked uv installation and offline image compatibility checks."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml
from packaging.specifiers import SpecifierSet

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ('pyproject.toml', 'uv.lock', '.python-version')


def test_all_installers_consume_the_same_uv_lock_and_ship_its_inputs():
    project = tomllib.loads((ROOT / 'backend/pyproject.toml').read_text())
    lock = tomllib.loads((ROOT / 'backend/uv.lock').read_text())
    assert SpecifierSet(lock['requires-python']) == SpecifierSet(project['project']['requires-python'])
    version = project['tool']['uv']['required-version'].removeprefix('==')
    docker = (ROOT / 'Dockerfile').read_text()
    native = (ROOT / 'scripts/linux/install_kylin.sh').read_text()
    ci_source = (ROOT / '.github/workflows/api-contracts.yml').read_text()
    assert f'ghcr.io/astral-sh/uv:{version}@sha256:' in docker
    for source in (docker, native, ci_source):
        assert 'sync --project backend --locked' in source
        assert '--no-build' in source
        assert 'pip install' not in source and 'requirements-' not in source
    for source in (docker, native):
        assert '--no-dev --extra sqlserver' in source
    ci = yaml.safe_load(ci_source)['jobs']['contracts']
    assert ci['env']['UV_PROJECT_ENVIRONMENT'] == '${{ github.workspace }}/.venv'
    assert ci['env']['UV_PYTHON_DOWNLOADS'] == 'never'
    setup = next(step for step in ci['steps'] if step.get('uses', '').startswith('astral-sh/setup-uv@'))
    assert setup['with']['version'] == version
    assert docker.count('python:3.12.14-slim-bookworm@sha256:') == 2
    assert 'msodbcsql18=18.6.2.1-1' in docker
    assert 'snapshot.debian.org/archive/debian/20261028T000000Z' in docker
    for name in INPUTS:
        assert f'backend/{name}' in docker
    assert 'ENTRYPOINT ["/usr/local/bin/cutting-entrypoint"]' in docker
    spec = importlib.util.spec_from_file_location('uv_packager', ROOT / 'scripts/build_docker_package.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {f'backend/{name}' for name in INPUTS} <= set(module.FILES)
    assert not any('requirements' in name or 'lock_python_dependencies' in name for name in module.FILES)
    assert not list((ROOT / 'backend').glob('requirements*.txt'))
    assert not (ROOT / 'scripts/lock_python_dependencies.py').exists()


@pytest.mark.parametrize('changed', [None, *INPUTS, 'missing-fingerprint'])
def test_image_entrypoint_checks_every_lock_input_before_running_application(tmp_path, changed):
    manifest = tmp_path / 'python-project.sha256'
    entries = []
    for name in INPUTS:
        path = tmp_path / name
        path.write_text(name)
        entries.append(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}\n')
    manifest.write_text(''.join(entries))
    if changed == 'missing-fingerprint':
        manifest.unlink()
    elif changed:
        (tmp_path / changed).write_text('changed')
    # macOS ships shasum; it supports the same checksum format and checker flags.
    binary_dir = tmp_path / 'bin'
    binary_dir.mkdir()
    if not shutil.which('sha256sum'):
        checker = binary_dir / 'sha256sum'
        checker.write_text(f'#!/bin/sh\nexec "{shutil.which("shasum")}" -a 256 "$@"\n')
        checker.chmod(0o755)
    script = tmp_path / 'entrypoint.sh'
    script.write_text((ROOT / 'scripts/linux/docker_entrypoint.sh').read_text().replace('/opt/python-project.sha256', str(manifest)))
    marker = tmp_path / 'started'
    result = subprocess.run(['sh', str(script), 'touch', str(marker)],
        env={**os.environ, 'PATH': f'{binary_dir}:{os.environ["PATH"]}'}, capture_output=True, text=True)
    assert result.returncode == (0 if changed is None else 1)
    assert marker.exists() == (changed is None)
    if changed:
        assert '依赖与运行镜像不一致' in result.stderr


@pytest.mark.parametrize('mismatch', [False, True])
def test_offline_preflight_rejects_mismatch_and_old_image_before_compose_up(tmp_path, mismatch):
    script = tmp_path / 'scripts/linux/start_offline_docker.sh'
    script.parent.mkdir(parents=True)
    script.write_text((ROOT / 'scripts/linux/start_offline_docker.sh').read_text())
    (tmp_path / '.env').write_text('APP_ENV=test\n')
    commands = tmp_path / 'commands'
    docker = tmp_path / 'docker'
    docker.write_text(f'''#!/bin/sh
printf '%s\\n' "$*" >> '{commands}'
case "$*" in
  *'config --images'*) echo cutting-report:test ;;
  run*) exit {1 if mismatch else 0} ;;
esac
''')
    docker.chmod(0o755)
    result = subprocess.run(['bash', str(script)], env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'}, capture_output=True, text=True)
    called = commands.read_text()
    for name in INPUTS:
        assert f'dst=/app/backend/{name},readonly' in called
    # An old entrypoint can pass its own obsolete requirements check. Requiring
    # the new fingerprint also rejects that image before services are replaced.
    assert 'test -r /opt/python-project.sha256' in called
    assert ('up -d' in called) != mismatch
    assert (result.returncode != 0) == mismatch


@pytest.mark.parametrize('existing', ['new', 'valid', 'unsupported', 'shared', 'invalid-directory'])
def test_native_install_uses_only_its_own_environment_and_preserves_configuration(tmp_path, existing):
    script = tmp_path / 'scripts/linux/install_kylin.sh'
    script.parent.mkdir(parents=True)
    script.write_text((ROOT / 'scripts/linux/install_kylin.sh').read_text())
    configuration = tmp_path / '.env'
    configuration.write_text('existing configuration\n')
    python = tmp_path / 'system-python'
    python.write_text(f'''#!/bin/sh
if [ "$1" = "-m" ] && [ "$2" = "playwright" ]; then exit 0; fi
exec "{sys.executable}" "$@"
''')
    python.chmod(0o755)
    venv = tmp_path / '.venv'
    if existing in ('valid', 'unsupported'):
        (venv / 'bin').mkdir(parents=True)
        interpreter = venv / 'bin/python'
        interpreter.write_text(python.read_text() if existing == 'valid' else '#!/bin/sh\nexit 2\n')
        interpreter.chmod(0o755)
    elif existing == 'shared':
        shared = tmp_path / 'shared'
        shared.mkdir()
        (shared / 'preserved').touch()
        venv.symlink_to(shared, target_is_directory=True)
    elif existing == 'invalid-directory':
        venv.mkdir()
        (venv / 'preserved').touch()
    invocation = tmp_path / 'uv-invocation.json'
    uv = tmp_path / 'uv'
    uv.write_text(f'''#!{sys.executable}
import json, os, pathlib, shutil, sys
pathlib.Path({str(invocation)!r}).write_text(json.dumps({{'args': sys.argv[1:], 'environment': os.environ['UV_PROJECT_ENVIRONMENT']}}))
interpreter = pathlib.Path(os.environ['UV_PROJECT_ENVIRONMENT']) / 'bin/python'
interpreter.parent.mkdir(parents=True, exist_ok=True)
if not interpreter.exists():
    shutil.copy2({str(python)!r}, interpreter)
''')
    uv.chmod(0o755)
    result = subprocess.run(['bash', str(script)], env={**os.environ, 'PYTHON_BIN': str(python), 'UV_BIN': str(uv)}, capture_output=True, text=True)
    assert configuration.read_text() == 'existing configuration\n'
    if existing not in ('new', 'valid'):
        assert result.returncode != 0 and not invocation.exists()
        if existing == 'shared':
            assert (tmp_path / 'shared/preserved').exists()
        elif existing == 'invalid-directory':
            assert (venv / 'preserved').exists()
        else:
            assert (venv / 'bin/python').read_text() == '#!/bin/sh\nexit 2\n'
        return
    assert result.returncode == 0, result.stderr
    call = json.loads(invocation.read_text())
    assert call['environment'] == str(venv)
    assert call['args'] == ['sync', '--project', 'backend', '--locked', '--no-dev', '--extra', 'sqlserver',
                            '--no-build', '--no-python-downloads', '--python', str(venv / 'bin/python' if existing == 'valid' else python)]
