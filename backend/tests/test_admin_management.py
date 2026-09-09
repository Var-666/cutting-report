from fastapi.testclient import TestClient

from app.main import app


class _AdminRepository:
    def __init__(self, admin: dict, *, password_valid: bool = True) -> None:
        self.admin = admin
        self.password_valid = password_valid
        self.grants = []

    def user_for_session(self, _token, *, now):
        return self.admin

    def verify_local_user_password(self, user_id, password):
        assert user_id == self.admin["user_id"]
        return self.password_valid and password == "current-password"

    def grant_external_admin(self, *, identity, operator, now):
        self.grants.append((identity, operator, now))
        return {
            "user_id": "usr_hr_admin",
            **identity,
            "username": identity["company_no"],
            "role": "admin",
            "auth_source": "hr_database",
            "active": True,
            "created_at": now,
        }


class _HrDirectory:
    def __init__(self) -> None:
        self.find_calls = []
        self.auth_calls = []

    def find_person(self, external_id):
        self.find_calls.append(external_id)
        return {
            "external_id": external_id,
            "company_no": "C0007",
            "group_no": "G0021",
            "display_name": "张三",
        }

    def authenticate(self, company_no, password):
        self.auth_calls.append((company_no, password))
        return {
            "external_id": "7001",
            "company_no": company_no,
            "group_no": "G0001",
            "display_name": "当前管理员",
        }


def _local_admin() -> dict:
    return {
        "user_id": "usr_local_admin",
        "username": "emergency-admin",
        "display_name": "应急管理员",
        "role": "admin",
        "auth_source": "local",
        "external_id": None,
        "company_no": None,
        "active": True,
        "created_at": "2026-09-09T08:00:00+08:00",
    }


def test_grant_hr_admin_requires_current_local_admin_password(monkeypatch) -> None:
    repository = _AdminRepository(_local_admin())
    directory = _HrDirectory()
    monkeypatch.setattr(app.state, "workshop", repository, raising=False)
    monkeypatch.setattr(app.state, "worker_auth", directory, raising=False)
    client = TestClient(app)
    client.cookies.set("cutting_session", "session")

    response = client.post(
        "/api/v1/auth/admins",
        json={"external_id": "9123", "current_password": "current-password"},
    )

    assert response.status_code == 201
    assert response.json()["data"]["role"] == "admin"
    assert directory.find_calls == ["9123"]
    assert repository.grants[0][0]["display_name"] == "张三"


def test_wrong_current_password_cannot_grant_admin(monkeypatch) -> None:
    repository = _AdminRepository(_local_admin(), password_valid=False)
    directory = _HrDirectory()
    monkeypatch.setattr(app.state, "workshop", repository, raising=False)
    monkeypatch.setattr(app.state, "worker_auth", directory, raising=False)
    client = TestClient(app)
    client.cookies.set("cutting_session", "session")

    response = client.post(
        "/api/v1/auth/admins",
        json={"external_id": "9123", "current_password": "wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "当前管理员密码错误"
    assert directory.find_calls == []
    assert repository.grants == []


def test_hr_admin_password_is_verified_against_hr_before_grant(monkeypatch) -> None:
    current = {
        **_local_admin(),
        "user_id": "usr_current_hr_admin",
        "username": "C0001",
        "company_no": "C0001",
        "external_id": "7001",
        "auth_source": "hr_database",
    }
    repository = _AdminRepository(current)
    directory = _HrDirectory()
    monkeypatch.setattr(app.state, "workshop", repository, raising=False)
    monkeypatch.setattr(app.state, "worker_auth", directory, raising=False)
    client = TestClient(app)
    client.cookies.set("cutting_session", "session")

    response = client.post(
        "/api/v1/auth/admins",
        json={"external_id": "9123", "current_password": "hr-password"},
    )

    assert response.status_code == 201
    assert directory.auth_calls == [("C0001", "hr-password")]


def test_anonymous_user_cannot_manage_admin_permissions(monkeypatch) -> None:
    class _AnonymousRepository:
        def user_for_session(self, _token, *, now):
            return None

    monkeypatch.setattr(
        app.state, "workshop", _AnonymousRepository(), raising=False
    )
    response = TestClient(app).post(
        "/api/v1/auth/admins",
        json={"external_id": "9123", "current_password": "password"},
    )

    assert response.status_code == 401
