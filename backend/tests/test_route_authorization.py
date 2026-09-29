from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import designer
from app.api.auth import LoginRequest, login, require_admin, require_user, require_worker
from app.config import settings
from app.database.workshop import AuthenticationError
from app.main import app


ADMIN_PREFIXES = (
    "/api/v1/reports",
    "/reports/",
    "/api/v1/options",
    "/api/v1/database",
    "/api/v1/designer",
    "/api/v1/leadership-report",
    "/api/v1/equipment-report",
    "/api/v1/energy-report",
    "/api/v1/analysis",
    "/api/v1/alarms",
    "/api/v1/llm-usage",
    "/api/v1/automation",
    "/api/v1/cutting",
    "/api/v1/conditions",
)


def _dependency_calls(route: APIRoute) -> set:
    return {dependency.call for dependency in route.dependant.dependencies}


def _api_routes() -> list[APIRoute]:
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        elif hasattr(route, "original_router"):
            routes.extend(
                nested
                for nested in route.original_router.routes
                if isinstance(nested, APIRoute)
            )
    return routes


def _route(path: str, method: str) -> APIRoute:
    return next(
        route
        for route in _api_routes()
        if route.path == path
        and method in route.methods
    )


def test_all_management_routes_require_an_admin_session() -> None:
    protected = [
        route
        for route in _api_routes()
        if route.path.startswith(ADMIN_PREFIXES)
    ]

    assert protected
    for route in protected:
        assert require_admin in _dependency_calls(route), route.path


def test_workshop_routes_keep_the_intended_role_split() -> None:
    for path, method in (
        ("/api/v1/workshop/latest-date", "GET"),
        ("/api/v1/workshop/reports", "POST"),
        ("/api/v1/workshop/tasks", "GET"),
    ):
        assert require_user in _dependency_calls(_route(path, method))

    confirmation = _route("/api/v1/workshop/tasks/{task_id}/confirm", "POST")
    assert require_worker in _dependency_calls(confirmation)
    batch_confirmation = _route("/api/v1/workshop/tasks/confirm", "POST")
    assert require_worker in _dependency_calls(batch_confirmation)
    revocation = _route("/api/v1/workshop/tasks/{task_id}/revoke", "POST")
    assert require_worker in _dependency_calls(revocation)
    correction = _route("/api/v1/workshop/tasks/{task_id}/correct", "POST")
    assert require_admin in _dependency_calls(correction)
    audits = _route("/api/v1/workshop/confirmation-audits", "GET")
    assert require_admin in _dependency_calls(audits)


def test_request_cancellation_requires_a_signed_in_user() -> None:
    cancellation = _route("/api/v1/requests/{request_id}/cancel", "POST")

    assert require_user in _dependency_calls(cancellation)


def test_public_bootstrap_login_and_health_routes_stay_public() -> None:
    for path, method in (
        ("/api/v1/auth/status", "GET"),
        ("/api/v1/auth/bootstrap", "POST"),
        ("/api/v1/auth/login", "POST"),
        ("/api/v1/auth/hr-login", "POST"),
        ("/api/v1/auth/worker-login", "POST"),
        ("/api/v1/health", "GET"),
    ):
        assert require_admin not in _dependency_calls(_route(path, method))


class _SessionStub:
    def __init__(self, user=None) -> None:
        self.user = user

    def user_for_session(self, _token, *, now):
        return self.user


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reports",
        "/api/v1/reports/not-found/download?format=pdf",
        "/api/v1/database/status",
        "/api/v1/designer/published-definitions",
        "/api/v1/leadership-report?report_month=2026-08",
        "/api/v1/equipment-report?report_date=2026-08-18",
        "/api/v1/energy-report/day?report_date=2026-08-18",
        "/api/v1/energy-report/tasks?report_date=2026-08-18",
        "/api/v1/energy-report/month?report_month=2026-08",
        "/api/v1/options/projects?report_date=2026-08-18",
        "/api/v1/automation/status",
        "/api/v1/alarms/status",
        "/api/v1/llm-usage",
        "/api/v1/cutting/status",
        "/api/v1/conditions/cutting_files/schema",
        "/reports/not-found/view",
    ],
)
def test_anonymous_requests_cannot_reach_management_endpoints(
    path: str, monkeypatch
) -> None:
    monkeypatch.setattr(app.state, "workshop", _SessionStub(), raising=False)

    response = TestClient(app).get(path)

    assert response.status_code == 401
    assert response.headers["x-session-expired"] == "1"


def test_worker_session_cannot_reach_management_endpoints(monkeypatch) -> None:
    worker = {
        "user_id": "usr_worker",
        "username": "worker",
        "display_name": "工人",
        "role": "worker",
        "active": True,
        "created_at": "2026-08-21T08:00:00+08:00",
    }
    monkeypatch.setattr(app.state, "workshop", _SessionStub(worker), raising=False)
    client = TestClient(app)
    client.cookies.set("cutting_session", "worker-session")

    response = client.get("/api/v1/database/status")

    assert response.status_code == 403


def test_anonymous_user_cannot_request_report_analysis(monkeypatch) -> None:
    monkeypatch.setattr(app.state, "workshop", _SessionStub(), raising=False)

    response = TestClient(app).post(
        "/api/v1/analysis",
        json={
            "report_type": "leadership",
            "report_date": "2026-08-01",
            "force": False,
        },
    )

    assert response.status_code == 401


def test_worker_cannot_pass_admin_dependency() -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_admin({"role": "worker"})

    assert exc_info.value.status_code == 403


def test_unified_login_rejects_a_legacy_local_worker_when_hr_credentials_fail(monkeypatch) -> None:
    class _LocalWorkerRepository:
        def authenticate(self, username, password):
            return {
                "user_id": "usr_local_worker",
                "username": username,
                "display_name": "旧工人",
                "role": "worker",
            }

    class _WorkerAuth:
        def authenticate(self, company_no, password):
            raise AuthenticationError("公司工号或密码错误")

    class _Request:
        app = type(
            "App",
            (),
            {
                "state": type(
                    "State",
                    (),
                    {"workshop": _LocalWorkerRepository(), "worker_auth": _WorkerAuth()},
                )()
            },
        )()

    monkeypatch.setattr(
        "app.api.auth.settings",
        replace(settings, worker_database_auth_enabled=True),
    )

    with pytest.raises(HTTPException) as exc_info:
        login(
            LoginRequest(username="old-worker", password="worker-password"),
            _Request(),
            object(),
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "账号或密码错误"


def test_unified_login_accepts_local_admin_without_contacting_hr(monkeypatch) -> None:
    class _LocalAdminRepository:
        def authenticate(self, username, password):
            assert (username, password) == ("system-admin", "local-password")
            return {
                "user_id": "usr_local_admin",
                "username": username,
                "display_name": "系统管理员",
                "role": "admin",
                "auth_source": "local",
            }

        def create_session(self, user_id, *, now, hours):
            assert user_id == "usr_local_admin"
            return "local-session-token", "2026-09-12T09:00:00+08:00"

    class _UnusedWorkerAuth:
        def authenticate(self, company_no, password):
            raise AssertionError("本地管理员登录不应访问HR数据库")

    monkeypatch.setattr(
        "app.api.auth.settings",
        replace(settings, worker_database_auth_enabled=True),
    )
    monkeypatch.setattr(app.state, "workshop", _LocalAdminRepository(), raising=False)
    monkeypatch.setattr(app.state, "worker_auth", _UnusedWorkerAuth(), raising=False)

    response = TestClient(app).post(
        "/api/v1/auth/login",
        json={"username": "system-admin", "password": "local-password"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["auth_source"] == "local"
    assert response.cookies.get("cutting_session") == "local-session-token"


def test_unified_login_accepts_hr_account_and_keeps_granted_role(monkeypatch) -> None:
    class _HrAccountRepository:
        def authenticate(self, username, password):
            raise AuthenticationError("账号或密码错误")

        def upsert_external_worker(self, **identity):
            assert identity["external_id"] == "9123"
            return {
                "user_id": "usr_hr_admin",
                "username": identity["company_no"],
                "display_name": identity["display_name"],
                "role": "admin",
                "auth_source": "hr_database",
            }

        def create_session(self, user_id, *, now, hours):
            assert user_id == "usr_hr_admin"
            return "hr-session-token", "2026-09-12T09:00:00+08:00"

    class _WorkerAuth:
        def authenticate(self, company_no, password):
            assert (company_no, password) == ("C0007", "p")
            return {
                "external_id": "9123",
                "company_no": "C0007",
                "group_no": "G0021",
                "display_name": "张三",
            }

    monkeypatch.setattr(
        "app.api.auth.settings",
        replace(settings, worker_database_auth_enabled=True),
    )
    monkeypatch.setattr(app.state, "workshop", _HrAccountRepository(), raising=False)
    monkeypatch.setattr(app.state, "worker_auth", _WorkerAuth(), raising=False)

    response = TestClient(app).post(
        "/api/v1/auth/login",
        json={"username": "C0007", "password": "p"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["role"] == "admin"
    assert response.cookies.get("cutting_session") == "hr-session-token"


def test_worker_login_creates_a_session_from_hr_identity(monkeypatch) -> None:
    class _WorkerAuthStub:
        def authenticate(self, company_no, password):
            assert company_no == "C0007"
            assert password == "plain-password"
            return {
                "external_id": "9123",
                "company_no": "C0007",
                "group_no": "G0021",
                "display_name": "张三",
            }

    class _WorkerSessionRepository:
        def upsert_external_worker(self, **identity):
            assert identity["external_id"] == "9123"
            assert "password" not in identity
            return {
                "user_id": "usr_hr_worker",
                "username": "C0007",
                "display_name": "张三",
                "role": "worker",
                "auth_source": "hr_database",
            }

        def create_session(self, user_id, *, now, hours):
            assert user_id == "usr_hr_worker"
            return "safe-session-token", "2026-08-24T21:00:00+08:00"

    monkeypatch.setattr(app.state, "worker_auth", _WorkerAuthStub(), raising=False)
    monkeypatch.setattr(
        app.state, "workshop", _WorkerSessionRepository(), raising=False
    )

    response = TestClient(app).post(
        "/api/v1/auth/worker-login",
        json={"company_no": "C0007", "password": "plain-password"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["user"]["role"] == "worker"
    assert response.cookies.get("cutting_session") == "safe-session-token"


@pytest.mark.parametrize("path", ["/admin/home", "/admin/reports/leadership"])
def test_frontend_admin_routes_return_the_spa_shell(path: str) -> None:
    response = TestClient(app).get(path)

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_unknown_api_route_is_not_rewritten_to_the_spa() -> None:
    response = TestClient(app).get("/api/v1/not-a-real-route")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_demo_flag_never_bypasses_sqlserver_designer_token(monkeypatch) -> None:
    sqlserver_settings = replace(
        settings,
        data_source_mode="sqlserver",
        demo_mode=True,
        sql_workbench_admin_token="",
    )
    monkeypatch.setattr(designer, "settings", sqlserver_settings)

    with pytest.raises(HTTPException) as exc_info:
        designer._require_admin_token(None)

    assert exc_info.value.status_code == 503


def test_excel_designer_uses_admin_session_without_a_second_token(monkeypatch) -> None:
    excel_settings = replace(
        settings,
        data_source_mode="excel",
        demo_mode=True,
        sql_workbench_admin_token="",
    )
    monkeypatch.setattr(designer, "settings", excel_settings)

    assert designer._require_admin_token(None) is None
