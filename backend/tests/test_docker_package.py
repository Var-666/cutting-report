import importlib.util
import json
from pathlib import Path
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("build_docker_package", ROOT / "scripts/build_docker_package.py")
packager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packager)


@pytest.mark.parametrize("name", [
    "backend/app/__pycache__/config.pyc", "frontend/src/.env", "frontend/src/.env.production",
    "frontend/public/TASK.xlsx", "frontend/dist/data.db", "frontend/dist/app.js.map",
    "frontend/node_modules/vue/index.js", "backend/app/.DS_Store",
])
def test_excludes_sensitive_or_generated_files(name):
    assert not packager.is_allowed(Path(name))


@pytest.mark.parametrize("name", [".env.example", "backend/app/api/energy.py", "frontend/dist/assets/app.js"])
def test_allows_required_files(name):
    assert packager.is_allowed(Path(name))


def test_source_package_keeps_soybean_build_configuration_and_license(tmp_path):
    required = (
        "backend/app/worker.py", "backend/app/database/coordination.py", "backend/app/api/workloads.py",
        "frontend/dist/index.html", "vendor/echarts.min.js",
        "frontend/dist/licenses/soybean-admin/LICENSE",
        "backend/app/api/energy.py", "backend/app/database/energy.py",
        "backend/app/services/energy_period_service.py",
        "frontend/src/views/EnergyPeriodReportView.vue",
        "backend/app/api/downloads.py", "backend/app/database/downloads.py",
        "backend/app/services/file_download_service.py",
        "frontend/src/views/CuttingDispatchView.vue",
        "backend/app/api/welding.py", "backend/app/database/welding.py",
        "backend/app/services/welding_service.py", "frontend/src/views/WeldingReportView.vue",
        "frontend/src/vendor/soybean-admin/LICENSE",
    )
    removed = (
        "START_DOCKER.bat", "scripts/windows/install_windows.bat", "scripts/windows/start_windows.bat",
        "docs/deployment/WINDOWS部署说明.md",
    )
    assert not set(removed).intersection(packager.FILES)
    for name in packager.FILES:
        assert (ROOT / name).is_file(), name
    for name in (*packager.FILES, *required):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    for name in ("vue", "vue-router", "echarts", "zrender", "tslib", "naive-ui", "pinia"):
        path = tmp_path / "frontend/node_modules" / name / "LICENSE"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name} license")

    payload = packager.collect_files(tmp_path)
    assert not set(removed).intersection(payload)
    assert payload["scripts/linux/start_kylin.sh"] == b"fixture"
    assert payload["scripts/linux/start_offline_docker.sh"] == b"fixture"
    assert payload["frontend/uno.config.js"] == b"fixture"
    assert payload["frontend/src/vendor/soybean-admin/LICENSE"] == b"fixture"
    assert payload["frontend/dist/licenses/soybean-admin/LICENSE"] == b"fixture"
    assert payload["licenses/naive-ui/LICENSE"] == b"naive-ui license"
    assert payload["licenses/pinia/LICENSE"] == b"pinia license"
    assert payload['scripts/check_welding.py'] == b'fixture'
    assert payload['docs/project/welding-integration.md'] == b'fixture'
    assert payload['backend/app/api/welding.py'] == b'fixture'
    for name in ("docs/deployment/配置参考.md", "docs/project/功能与接口参考.md", "docs/project/changelog.md"):
        assert payload[name] == b"fixture"


def test_archive_has_manifest_and_does_not_replace_existing_release(tmp_path, monkeypatch):
    payload = {"backend/app/api/energy.py": b"# feature\n", ".env.example": b"PASSWORD=\n"}
    monkeypatch.setattr(packager, "collect_files", lambda root: payload.copy())
    result = packager.build_package(tmp_path, "test-release")
    original = result.read_bytes()
    with zipfile.ZipFile(result) as archive:
        manifest = json.loads(archive.read("test-release/PACKAGE-MANIFEST.json"))
        assert manifest["offline_image_included"] is False
        assert manifest["deployment_env_included"] is False
        for path, expected in manifest["files"].items():
            assert packager.hashlib.sha256(archive.read("test-release/" + path)).hexdigest() == expected
        assert "test-release/.env" not in archive.namelist()
    with pytest.raises(FileExistsError):
        packager.build_package(tmp_path, "test-release")
    assert result.read_bytes() == original


def test_archive_maps_local_deployment_env_to_root_env(tmp_path, monkeypatch):
    deployment_env = b"APP_ENV=production\nDOCKER_PORT=8088\n"
    (tmp_path / ".env.deploy").write_bytes(deployment_env)
    monkeypatch.setattr(packager, "collect_files", lambda root: {".env.example": b"APP_ENV=development\n"})

    result = packager.build_package(tmp_path, "test-with-env")

    with zipfile.ZipFile(result) as archive:
        names = archive.namelist()
        assert "test-with-env/.env" in names
        assert "test-with-env/.env.deploy" not in names
        assert archive.read("test-with-env/.env") == deployment_env
        manifest = json.loads(archive.read("test-with-env/PACKAGE-MANIFEST.json"))
        assert manifest["deployment_env_included"] is True
        assert manifest["files"][".env"] == packager.hashlib.sha256(deployment_env).hexdigest()


@pytest.mark.parametrize("name", ["../bad", "a/b", "", "a\\b"])
def test_rejects_path_traversal(tmp_path, name):
    with pytest.raises(ValueError):
        packager.build_package(tmp_path, name)
