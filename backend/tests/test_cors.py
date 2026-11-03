from dataclasses import replace
from pathlib import Path
import runpy

import pytest
from fastapi.testclient import TestClient

from app import config


@pytest.mark.parametrize('origin, status', [
    ('https://frontend.example', 200),
    ('https://untrusted.example', 400),
])
def test_data_source_patch_preflight_respects_allowed_origins(monkeypatch, origin, status):
    monkeypatch.setattr(config, 'settings', replace(
        config.settings, cors_origins=('https://frontend.example',)))
    app = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'app/main.py'))['app']
    response = TestClient(app).options('/api/v1/database/sources/quality/enabled', headers={
        'Origin': origin,
        'Access-Control-Request-Method': 'PATCH',
        'Access-Control-Request-Headers': 'Content-Type',
    })
    assert response.status_code == status
    if status == 200:
        assert response.headers['access-control-allow-origin'] == origin
        assert 'PATCH' in response.headers['access-control-allow-methods'].split(', ')
    else:
        assert 'access-control-allow-origin' not in response.headers
