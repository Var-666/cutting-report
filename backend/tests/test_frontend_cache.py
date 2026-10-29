from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.frontend_static import FrontendAssets


def test_index_and_nested_routes_are_not_stored_without_caching_api_errors(tmp_path, monkeypatch):
    from app import main
    dist = tmp_path / 'frontend/dist'
    dist.mkdir(parents=True)
    (dist / 'index.html').write_text('<html>frontend</html>')
    monkeypatch.setattr(main, 'settings', replace(main.settings, project_root=tmp_path))
    client = TestClient(main.app)
    for path in ('/', '/admin/home', '/admin/welding/reports/jobs'):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
    response = client.get('/api/v1/not-a-real-route')
    assert response.status_code == 404
    assert 'cache-control' not in response.headers


def test_only_fingerprinted_js_and_css_are_cached_for_one_year(tmp_path):
    for filename in ('index-Abc123_X.js', 'style-1234abcd.css', 'plain.js'):
        (tmp_path / filename).write_text('test')
    app = FastAPI()
    app.mount('/assets', FrontendAssets(directory=tmp_path))
    client = TestClient(app)
    for filename in ('index-Abc123_X.js', 'style-1234abcd.css'):
        response = client.get('/assets/' + filename)
        assert response.headers['cache-control'] == 'public, max-age=31536000, immutable'
        conditional = client.get('/assets/' + filename, headers={'If-None-Match': response.headers['etag']})
        assert conditional.status_code == 304
        assert conditional.headers['cache-control'] == response.headers['cache-control']
    assert 'cache-control' not in client.get('/assets/plain.js').headers
    assert 'cache-control' not in client.get('/assets/missing-1234abcd.js').headers
