"""Generate hash-locked pip inputs from uv.lock, or check them without modifying files."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROFILES = {
    'requirements.txt': ['--no-dev'],
    'requirements-dev.txt': [],
    'requirements-sqlserver.txt': ['--no-dev', '--extra', 'sqlserver'],
}
HEADER = '# Generated from backend/pyproject.toml and uv.lock; do not edit.\n# Update: python scripts/lock_python_dependencies.py\n'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    uv = os.environ.get('UV', 'uv')
    for name, profile in PROFILES.items():
        result = subprocess.run(
            [uv, 'export', '--project', str(ROOT / 'backend'), '--locked', '--no-emit-project',
             '--no-header', '--no-annotate', *profile],
            check=True, capture_output=True, text=True,
        )
        expected = HEADER + result.stdout
        target = ROOT / 'backend' / name
        if args.check:
            if not target.is_file() or target.read_text() != expected:
                raise SystemExit(f'{name} is stale; run python scripts/lock_python_dependencies.py')
        else:
            target.write_text(expected)
        print(f'{"Checked" if args.check else "Generated"} backend/{name}')


if __name__ == '__main__':
    main()
