"""Checks for the locked Python install and offline image compatibility boundary."""
import os
from pathlib import Path
import subprocess
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]


def requirements(name):
    text = (ROOT / 'backend' / name).read_text().replace('\\\n', ' ')
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        declaration, *hashes = line.split('--hash=sha256:')
        requirement = Requirement(declaration.strip())
        pins = list(requirement.specifier)
        assert len(pins) == 1 and pins[0].operator == '=='
        assert hashes and all(len(value.strip()) == 64 for value in hashes)
        result[canonicalize_name(requirement.name)] = pins[0].version
    return result


def test_exports_pin_complete_profiles_to_the_same_lock():
    lock = tomllib.loads((ROOT / 'backend/uv.lock').read_text())
    versions = {item['name']: item['version'] for item in lock['package']}
    base = requirements('requirements.txt')
    development = requirements('requirements-dev.txt')
    sqlserver = requirements('requirements-sqlserver.txt')
    for profile in (base, development, sqlserver):
        assert all(versions[name] == version for name, version in profile.items())
    assert base.items() <= development.items()
    assert base.items() <= sqlserver.items()
    assert 'pytest' not in base and 'pytest' not in sqlserver
    assert {'pytest', 'pytest-cov'} <= development.keys()
    assert sqlserver.keys() - base.keys() == {'pyodbc'}


def test_docker_and_native_installs_consume_hashes_and_package_all_lock_inputs():
    docker = (ROOT / 'Dockerfile').read_text()
    native = (ROOT / 'scripts/linux/install_kylin.sh').read_text()
    assert '--require-hashes --no-deps --only-binary=:all:' in docker
    assert '--require-hashes --no-deps --only-binary=:all:' in native
    assert 'pip install --upgrade pip' not in docker + native
    assert docker.count('python:3.12.14-slim-bookworm@sha256:') == 2
    assert (ROOT / 'backend/.python-version').read_text().strip() == '3.12.14'
    assert 'msodbcsql18=18.6.2.1-1' in docker
    assert 'snapshot.debian.org/archive/debian/20261028T000000Z' in docker
    assert 'ENTRYPOINT ["/usr/local/bin/cutting-entrypoint"]' in docker
    import importlib.util
    spec = importlib.util.spec_from_file_location('lock_packager', ROOT / 'scripts/build_docker_package.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert {'backend/uv.lock', 'backend/.python-version', 'backend/pyproject.toml',
            'backend/requirements.txt', 'backend/requirements-dev.txt', 'backend/requirements-sqlserver.txt',
            'scripts/lock_python_dependencies.py', 'scripts/linux/docker_entrypoint.sh'} <= set(module.FILES)


@pytest.mark.parametrize('mismatch', [False, True])
def test_image_entrypoint_blocks_mismatch_before_running_application(tmp_path, mismatch):
    stub = tmp_path / 'sha256sum'
    stub.write_text(f'#!/bin/sh\nexit {1 if mismatch else 0}\n')
    stub.chmod(0o755)
    marker = tmp_path / 'started'
    result = subprocess.run(
        ['sh', str(ROOT / 'scripts/linux/docker_entrypoint.sh'), 'sh', '-c', f'touch "{marker}"'],
        env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'}, capture_output=True, text=True,
    )
    assert (result.returncode != 0) == mismatch
    assert marker.exists() != mismatch
    if mismatch:
        assert '依赖与运行镜像不一致' in result.stderr


def test_offline_mismatch_prevents_compose_up(tmp_path):
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
  run*) exit 1 ;;
esac
''')
    docker.chmod(0o755)
    result = subprocess.run(['bash', str(script)], env={**os.environ, 'PATH': f'{tmp_path}:{os.environ["PATH"]}'}, capture_output=True, text=True)
    assert result.returncode != 0
    called = commands.read_text()
    assert 'run --rm --network none' in called
    assert 'up -d' not in called
