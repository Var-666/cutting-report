from dataclasses import replace
from datetime import datetime
from threading import Event, Lock
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import anyio
import httpx
import pytest

from app.api import auth, workloads
from app.config import settings
from app.database import users as workshop
from app.database.workshop import WorkshopRepository
from app.main import app
from app.services.worker_auth_service import WorkerAuthService
from test_worker_auth_service import FakeSqlServerClient, _enabled_settings, _valid_row


@pytest.mark.parametrize('blocking_kind', ['password', 'hr'])
def test_eight_expensive_authentications_leave_local_and_cached_hr_business_requests_free(tmp_path, monkeypatch, blocking_kind):
    repository = WorkshopRepository(tmp_path / 'metadata.db')
    repository.initialize()
    now = datetime.now(ZoneInfo(settings.timezone))
    admin = repository.bootstrap_admin(username='local-admin', display_name='管理员', password='test-password', now=now.isoformat())
    local_token, _ = repository.create_session(admin['user_id'], now=now, hours=1)
    hr_user = repository.upsert_external_worker(external_id='9123', company_no='C0007', group_no='G0021', display_name='工人', now=now.isoformat())
    hr_token, _ = repository.create_session(hr_user['user_id'], now=now, hours=1)
    source = FakeSqlServerClient([_valid_row()])
    service = WorkerAuthService(_enabled_settings(), source)
    service.authenticate('C0007', 'password')  # Prime the real HR status cache.
    release, all_entered = Event(), Event()
    lock = Lock()
    calls = 0
    original_verify = workshop.verify_password
    original_fetch = source.fetch_all

    def block(operation, *args):
        nonlocal calls
        with lock:
            calls += 1
            if calls == 8:
                all_entered.set()
        assert release.wait(5)
        return operation(*args)

    if blocking_kind == 'password':
        monkeypatch.setattr(workshop, 'verify_password', lambda *args: block(original_verify, *args))
        slow_path, slow_payload = '/api/v1/auth/login', {'username': 'local-admin', 'password': 'test-password'}
    else:
        monkeypatch.setattr(source, 'fetch_all', lambda *args: block(original_fetch, *args))
        slow_path, slow_payload = '/api/v1/auth/hr-login', {'company_no': 'C0007', 'password': 'password'}

    monkeypatch.setattr(auth, 'settings', replace(settings, worker_database_auth_enabled=True))
    monkeypatch.setattr(app.state, 'workshop', repository, raising=False)
    monkeypatch.setattr(app.state, 'worker_auth', service, raising=False)
    runtime = SimpleNamespace(settings=settings, metadata=SimpleNamespace(list_reports=lambda **_: ([], 0)),
                              energy_period_service=SimpleNamespace(get_day=lambda *_, **__: {}))
    monkeypatch.setattr(app.state, 'devices', {'Cutter01': runtime}, raising=False)
    local_headers = {'Cookie': f'cutting_session={local_token}'}
    hr_headers = {'Cookie': f'cutting_session={hr_token}'}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            completed = []

            async def authenticate():
                completed.append(await client.post(slow_path, json=slow_payload))

            async with anyio.create_task_group() as tasks:
                try:
                    for _ in range(8):
                        tasks.start_soon(authenticate)
                    with anyio.fail_after(2):
                        while not all_entered.is_set():
                            await anyio.sleep(0.01)
                    assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
                    assert workloads.authentication_requests._threads.borrowed_tokens == 8
                    responses = []

                    async def business(path, headers):
                        responses.append(await client.get(path, headers=headers))

                    with anyio.fail_after(2):
                        async with anyio.create_task_group() as business_tasks:
                            for path in ['/api/v1/devices', '/api/v1/auth/status',
                                         '/api/v1/reports?device_id=Cutter01',
                                         '/api/v1/energy-report/day?report_date=2026-10-24&device_id=Cutter01'] * 4:
                                business_tasks.start_soon(business, path, local_headers)
                            for _ in range(12):
                                business_tasks.start_soon(business, '/api/v1/devices', hr_headers)
                    assert len(responses) == 28 and all(response.status_code == 200 for response in responses)
                    with anyio.fail_after(2):
                        for path, payload in [('/api/v1/auth/login', slow_payload if blocking_kind == 'password' else {'username': 'a', 'password': 'b'}),
                                              ('/api/v1/auth/hr-login', {'company_no': 'C0007', 'password': 'password'}),
                                              ('/api/v1/auth/worker-login', {'company_no': 'C0007', 'password': 'password'}),
                                              ('/api/v1/auth/bootstrap', {'username': 'another-admin', 'display_name': '管理员', 'password': 'test-password'}),
                                              ('/api/v1/auth/users', {'username': 'another-worker', 'display_name': '工人', 'password': 'test-password'}),
                                              ('/api/v1/auth/admins', {'external_id': '9123', 'current_password': 'test-password'}),
                                              ('/api/v1/auth/admins/target/revoke', {'current_password': 'test-password'})]:
                            response = await client.post(path, json=payload, headers=local_headers)
                            assert response.status_code == 503, (path, response.text)
                            assert response.headers['retry-after'] == '1'
                        assert (await client.get('/api/v1/auth/hr-directory?query=张', headers=local_headers)).status_code == 503
                        forced = await client.post('/api/v1/requests/pending/cancel', headers=hr_headers)
                        assert forced.status_code == 503 and 'X-Session-Expired' not in forced.headers
                        assert (await client.get('/api/v1/devices', headers={'Cookie': ''})).status_code == 401
                        assert (await client.get('/api/v1/reports?device_id=Cutter01', headers=hr_headers)).status_code == 403
                    assert calls == 8, 'cached reads and rejected calls must not start expensive work'
                finally:
                    release.set()
            assert len(completed) == 8 and all(response.status_code == 200 for response in completed)
            assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 0
            assert workloads.authentication_requests._threads.borrowed_tokens == 0
        assert repository.user_for_session(local_token, now=now.isoformat())
        assert repository.user_for_session(hr_token, now=now.isoformat())
        assert len(source.calls) == (1 if blocking_kind == 'password' else 9)

    anyio.run(exercise)


def test_more_than_eight_session_reads_run_in_threads_without_authentication_admission(monkeypatch):
    entered, release = Event(), Event()
    lock = Lock()
    count = 0
    user = {'user_id': 'admin', 'role': 'admin', 'auth_source': 'local'}

    def lookup(*_, **__):
        nonlocal count
        with lock:
            count += 1
            if count == 12:
                entered.set()
        assert release.wait(5)
        return user

    monkeypatch.setattr(app.state, 'workshop', SimpleNamespace(user_for_session=lookup), raising=False)
    monkeypatch.setattr(app.state, 'devices', {}, raising=False)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            responses = []

            async def request():
                responses.append(await client.get('/api/v1/devices'))

            async with anyio.create_task_group() as tasks:
                try:
                    for _ in range(12):
                        tasks.start_soon(request)
                    with anyio.fail_after(2):
                        while not entered.is_set():
                            await anyio.sleep(0.01)
                    assert anyio.to_thread.current_default_thread_limiter().borrowed_tokens == 12
                    for _ in range(8):
                        assert workloads.authentication_requests._slots.acquire(blocking=False)
                    try:
                        assert (await client.get('/api/v1/health')).status_code == 200
                    finally:
                        for _ in range(8):
                            workloads.authentication_requests._slots.release()
                finally:
                    release.set()
            assert len(responses) == 12 and all(response.status_code == 200 for response in responses)

    anyio.run(exercise)


def test_bootstrap_and_local_account_hashing_use_authentication_capacity_and_keep_sessions(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    repository = WorkshopRepository(tmp_path / 'metadata.db')
    repository.initialize()
    isolated = FastAPI()
    isolated.include_router(auth.router)
    isolated.state.workshop = repository

    @isolated.middleware('http')
    async def request_id(request, call_next):
        request.state.request_id = 'isolated-auth-test'
        return await call_next(request)

    monkeypatch.setattr(auth, 'settings', replace(settings, worker_database_auth_enabled=False, auth_cookie_secure=False))
    original_hash = workshop.hash_password
    hashes = []

    def hash_password(password):
        assert workloads.authentication_requests._threads.borrowed_tokens == 1
        hashes.append(password)
        return original_hash(password)

    monkeypatch.setattr(workshop, 'hash_password', hash_password)
    with TestClient(isolated) as client:
        response = client.post('/api/v1/auth/bootstrap', json={
            'username': 'local-admin', 'display_name': '管理员', 'password': 'admin-password',
        })
        assert response.status_code == 200
        assert response.cookies.get('cutting_session')
        assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=lax' in response.headers['set-cookie']
        response = client.post('/api/v1/auth/users', json={
            'username': 'local-worker', 'display_name': '工人', 'password': 'worker-password',
        })
        assert response.status_code == 201
        assert hashes == ['admin-password', 'worker-password']
        assert client.get('/api/v1/auth/status').json()['data']['authenticated'] is True
