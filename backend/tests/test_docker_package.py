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


def test_archive_has_manifest_and_does_not_replace_existing_release(tmp_path, monkeypatch):
    payload = {"backend/app/api/energy.py": b"# feature\n", ".env.example": b"PASSWORD=\n"}
    monkeypatch.setattr(packager, "collect_files", lambda root: payload.copy())
    result = packager.build_package(tmp_path, "test-release")
    original = result.read_bytes()
    with zipfile.ZipFile(result) as archive:
        manifest = json.loads(archive.read("test-release/PACKAGE-MANIFEST.json"))
        assert manifest["offline_image_included"] is False
        for path, expected in manifest["files"].items():
            assert packager.hashlib.sha256(archive.read("test-release/" + path)).hexdigest() == expected
        assert "test-release/.env" not in archive.namelist()
    with pytest.raises(FileExistsError):
        packager.build_package(tmp_path, "test-release")
    assert result.read_bytes() == original


@pytest.mark.parametrize("name", ["../bad", "a/b", "", "a\\b"])
def test_rejects_path_traversal(tmp_path, name):
    with pytest.raises(ValueError):
        packager.build_package(tmp_path, name)
