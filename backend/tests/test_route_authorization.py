from dataclasses import replace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api import designer
from app.api.auth import require_admin, require_user, require_worker
from app.config import settings
from app.main import app


ADMIN_PREFIXES = (
    "/api/v1/reports",
    "/reports/",
    "/api/v1/options",
    "/api/v1/database",
    "/api/v1/designer",
    "/api/v1/leadership-report",
    "/api/v1/equipment-report",
    "/api/v1/analysis",
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


def test_public_bootstrap_login_and_health_routes_stay_public() -> None:
    for path, method in (
        ("/api/v1/auth/status", "GET"),
        ("/api/v1/auth/bootstrap", "POST"),
        ("/api/v1/auth/login", "POST"),
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
        "/api/v1/database/status",
        "/api/v1/designer/published-definitions",
        "/api/v1/leadership-report?report_month=2026-08",
        "/api/v1/equipment-report?report_date=2026-08-18",
        "/api/v1/options/projects?report_date=2026-08-18",
        "/reports/not-found/view",
    ],
)
def test_anonymous_requests_cannot_reach_management_endpoints(
    path: str, monkeypatch
) -> None:
    monkeypatch.setattr(app.state, "workshop", _SessionStub(), raising=False)

    response = TestClient(app).get(path)

    assert response.status_code == 401


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
