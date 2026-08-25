from dataclasses import replace

import pytest

from app.config import settings
from app.database.workshop import AuthenticationError
from app.services.worker_auth_service import (
    WorkerAuthenticationUnavailableError,
    WorkerAuthService,
)


class FakeSqlServerClient:
    def __init__(self, rows=None, error: Exception | None = None) -> None:
        self.rows = rows or []
        self.error = error
        self.calls = []

    def fetch_all(self, sql, parameters):
        self.calls.append((sql, parameters))
        if self.error:
            raise self.error
        return self.rows


def _enabled_settings(**changes):
    return replace(settings, worker_database_auth_enabled=True, **changes)


def _valid_row(**changes):
    row = {
        "external_id": 9123,
        "display_name": "张三",
        "group_no": "G0021",
        "company_no": "C0007",
        "available_value": 1,
        "locked_value": 0,
        "password_matches": 1,
    }
    row.update(changes)
    return row


def test_worker_auth_uses_company_number_and_keeps_password_out_of_select() -> None:
    client = FakeSqlServerClient([_valid_row()])
    service = WorkerAuthService(_enabled_settings(), client=client)

    identity = service.authenticate(" C0007 ", "plain-password")

    assert identity == {
        "external_id": "9123",
        "company_no": "C0007",
        "group_no": "G0021",
        "display_name": "张三",
    }
    sql, parameters = client.calls[0]
    assert parameters == ("plain-password", "C0007")
    assert "[密码] AS" not in sql
    assert "WHERE [公司工号] = ?" in sql


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_valid_row(password_matches=0)],
        [_valid_row(available_value=0)],
        [_valid_row(locked_value=1)],
        [_valid_row(), _valid_row(external_id=9124)],
    ],
)
def test_worker_auth_rejects_invalid_or_ambiguous_accounts(rows) -> None:
    service = WorkerAuthService(_enabled_settings(), client=FakeSqlServerClient(rows))

    with pytest.raises(AuthenticationError, match="公司工号或密码错误"):
        service.authenticate("C0007", "wrong-or-disabled")


def test_worker_auth_state_values_are_configurable() -> None:
    client = FakeSqlServerClient(
        [_valid_row(available_value="是", locked_value="否")]
    )
    service = WorkerAuthService(
        _enabled_settings(
            worker_database_available_value="是",
            worker_database_unlocked_value="否",
        ),
        client=client,
    )

    assert service.authenticate("C0007", "password")["external_id"] == "9123"


def test_worker_auth_reports_configuration_or_connection_outage() -> None:
    disabled = WorkerAuthService(
        replace(settings, worker_database_auth_enabled=False),
        client=FakeSqlServerClient(),
    )
    with pytest.raises(WorkerAuthenticationUnavailableError):
        disabled.authenticate("C0007", "password")

    unavailable = WorkerAuthService(
        _enabled_settings(),
        client=FakeSqlServerClient(error=RuntimeError("network unavailable")),
    )
    with pytest.raises(WorkerAuthenticationUnavailableError):
        unavailable.authenticate("C0007", "password")
