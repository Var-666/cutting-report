
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.mark.parametrize('origin, status', [
    ('https://frontend.example', 200),
    ('https://untrusted.example', 400),
])
def test_data_source_patch_preflight_respects_allowed_origins(origin, status):
    app = create_app(Settings(cors_origins=('https://frontend.example',)))
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
