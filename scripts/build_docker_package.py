"""Build a clean Windows/Docker source ZIP from the current working tree.

Includes uncommitted feature files deliberately, but never runtime data or the
working-tree .env. When .env.deploy exists, it is added to the ZIP as .env.
Run after tests: python scripts/build_docker_package.py --name RELEASE_NAME
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEPLOYMENT_ENV_FILE = ".env.deploy"
FILES = (
    "Dockerfile", "compose.yaml", "compose.offline.yaml", ".dockerignore", ".env.example", "README.md",
    "README-DOCKER.md", "START_DOCKER.bat", "backend/requirements.txt",
    "backend/requirements-sqlserver.txt", "backend/pyproject.toml",
    "frontend/package.json", "frontend/package-lock.json", "frontend/index.html",
    "frontend/vite.config.js", "docs/deployment/WINDOWS部署说明.md",
    "docs/deployment/KYLIN原生部署说明.md",
    "docs/deployment/OFFLINE_DOCKER部署说明.md",
    "docker/input/README.md", "scripts/README.md", "scripts/check_energy.py",
    "scripts/build_docker_package.py", "scripts/windows/install_windows.bat",
    "scripts/windows/start_windows.bat", "scripts/linux/install_kylin.sh",
    "scripts/linux/start_kylin.sh", "scripts/linux/start_offline_docker.sh",
)
TREES = (
    "backend/app", "frontend/src", "frontend/public", "frontend/scripts",
    "frontend/dist", "vendor",
)
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", "node_modules", ".git", ".DS_Store"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".xlsx", ".db", ".sqlite", ".sqlite3", ".log", ".zip", ".map"}


def is_allowed(path: Path) -> bool:
    return (
        not EXCLUDED_PARTS.intersection(path.parts)
        and path.suffix.lower() not in EXCLUDED_SUFFIXES
        and (not path.name.startswith(".env") or path.name == ".env.example")
    )


def collect_files(root: Path) -> dict[str, bytes]:
    paths = {root / name for name in FILES}
    for directory in TREES:
        paths.update(path for path in (root / directory).rglob("*") if path.is_file())
    payload = {}
    for path in sorted(paths):
        relative = path.relative_to(root)
        if not is_allowed(relative):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Cannot package symlink: {relative}")
        content = path.read_bytes()
        if path.suffix == ".bat":
            content = content.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        payload[relative.as_posix()] = content
    required = (
        "frontend/dist/index.html", "vendor/echarts.min.js",
        "backend/app/api/energy.py", "backend/app/database/energy.py",
        "backend/app/services/energy_period_service.py",
        "frontend/src/views/EnergyPeriodReportView.vue",
        "backend/app/api/downloads.py", "backend/app/database/downloads.py",
        "backend/app/services/file_download_service.py",
        "frontend/src/views/CuttingDispatchView.vue",
    )
    for name in required:
        if name not in payload:
            raise ValueError(f"Missing required build/feature file: {name}")
    # Retain notices for the browser runtime libraries already included in the build.
    for package in ("vue", "vue-router", "echarts", "zrender", "tslib"):
        package_root = root / "frontend" / "node_modules" / package
        notices = [path for path in package_root.glob("*")
                   if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))]
        if not notices:
            raise ValueError(f"Missing third-party notice: {package}")
        for path in notices:
            payload[f"licenses/{package}/{path.name}"] = path.read_bytes()
    return payload


def add_deployment_env(root: Path, payload: dict[str, bytes]) -> bool:
    """Map the local, Git-ignored deployment configuration to ZIP-root .env."""
    source = root / DEPLOYMENT_ENV_FILE
    if not source.exists():
        return False
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"{DEPLOYMENT_ENV_FILE} must be a regular file")
    content = source.read_bytes()
    if not content.strip():
        raise ValueError(f"{DEPLOYMENT_ENV_FILE} cannot be empty")
    payload[".env"] = content
    return True


def build_package(root: Path, name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise ValueError("Release name must contain only letters, numbers, dots, hyphens or underscores")
    payload = collect_files(root)
    deployment_env_included = add_deployment_env(root, payload)
    manifest = {
        "release": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "kind": "docker-source-package",
        "offline_image_included": False,
        "deployment_env_included": deployment_env_included,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(payload.items())},
    }
    payload["PACKAGE-MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    destination = root / "packages" / f"{name}.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never replace a previously delivered release archive.
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative, content in sorted(payload.items()):
            archive_path = f"{name}/{relative}"
            info = zipfile.ZipInfo(archive_path)
            info.create_system = 3
            # The image runs as an unprivileged user. Files mounted from a Linux
            # host must therefore be world-readable after root extracts the ZIP.
            # Keep the deployment environment private and make shell scripts
            # directly executable.
            mode = 0o600 if relative == ".env" else 0o755 if relative.endswith(".sh") else 0o644
            info.external_attr = mode << 16
            archive.writestr(
                info,
                content,
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        for relative, content in payload.items():
            if archive.read(f"{name}/{relative}") != content:
                raise ValueError(f"ZIP content mismatch: {relative}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--skip-frontend-build", action="store_true", help="Only when npm run build has just succeeded")
    args = parser.parse_args()
    if not args.skip_frontend_build:
        subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
    result = build_package(ROOT, args.name)
    print(f"Created: {result}")
    print(f"Deployment .env included: {'yes' if (ROOT / DEPLOYMENT_ENV_FILE).is_file() else 'no'}")
    print(f"Size: {result.stat().st_size:,} bytes")
    print(f"SHA256: {hashlib.sha256(result.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
