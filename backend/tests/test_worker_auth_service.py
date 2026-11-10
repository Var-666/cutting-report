from tests.auth_fixtures import FakeSqlServerClient, enabled_settings as _enabled_settings, valid_row as _valid_row
from dataclasses import replace

import pytest

from app.config import settings
from app.database.workshop import AuthenticationError
from app.services.worker_auth_service import (
    WorkerAuthenticationUnavailableError,
    WorkerAuthService,
)


def test_worker_auth_uses_group_number_and_keeps_password_out_of_select() -> None:
    client = FakeSqlServerClient([_valid_row()])
    service = WorkerAuthService(_enabled_settings(), client=client)

    identity = service.authenticate(" G0021 ", "plain-password")

    assert identity == {
        "external_id": "9123",
        "company_no": "C0007",
        "group_no": "G0021",
        "display_name": "张三",
    }
    sql, parameters = client.calls[0]
    assert parameters == ("plain-password", "G0021")
    assert "[密码] AS" not in sql
    assert "WHERE [集团工号] = ?" in sql
    assert "WHERE [公司工号] = ?" not in sql
    assert "_Locked" not in sql
    assert "locked_value" not in sql


def test_fresh_hr_profile_read_replaces_a_stale_session_availability():
    client = FakeSqlServerClient([])
    service = WorkerAuthService(_enabled_settings(), client=client)
    assert service.session_identity_available('9123') is False
    client.rows = [_valid_row()]
    assert service.find_person('9123')['group_no'] == 'G0021'
    assert service.cached_session_identity_available('9123') is True
    client.rows = []
    assert service.find_person('9123') is None
    assert service.cached_session_identity_available('9123') is False


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [_valid_row(password_matches=0)],
        [_valid_row(available_value=0)],
        [_valid_row(available_value=None)],
        [_valid_row(group_no=None)],
        [_valid_row(group_no="   ")],
        [_valid_row(locked_value=None, password_matches=0)],
        [_valid_row(locked_value=1, available_value=0)],
        [_valid_row(), _valid_row(external_id=9124)],
    ],
)
def test_worker_auth_rejects_invalid_or_ambiguous_accounts(rows) -> None:
    service = WorkerAuthService(_enabled_settings(), client=FakeSqlServerClient(rows))

    with pytest.raises(AuthenticationError, match="集团工号或密码错误"):
        service.authenticate("G0021", "wrong-or-disabled")


@pytest.mark.parametrize("locked_value", [None, 0, 1, "", "否", "是"])
def test_worker_auth_does_not_check_locked_value(locked_value) -> None:
    client = FakeSqlServerClient([_valid_row(locked_value=locked_value)])
    service = WorkerAuthService(_enabled_settings(), client=client)

    assert service.authenticate("G0021", "password")["external_id"] == "9123"


def test_worker_auth_available_value_is_configurable() -> None:
    client = FakeSqlServerClient([_valid_row(available_value="是")])
    service = WorkerAuthService(
        _enabled_settings(
            worker_database_available_value="是",
        ),
        client=client,
    )

    assert service.authenticate("G0021", "password")["external_id"] == "9123"


def test_worker_auth_reports_configuration_or_connection_outage() -> None:
    disabled = WorkerAuthService(
        replace(settings, worker_database_auth_enabled=False),
        client=FakeSqlServerClient(),
    )
    with pytest.raises(WorkerAuthenticationUnavailableError):
        disabled.authenticate("G0021", "password")

    unavailable = WorkerAuthService(
        _enabled_settings(),
        client=FakeSqlServerClient(error=RuntimeError("network unavailable")),
    )
    with pytest.raises(WorkerAuthenticationUnavailableError):
        unavailable.authenticate("G0021", "password")


def test_hr_directory_search_never_reads_or_returns_passwords() -> None:
    client = FakeSqlServerClient([
        _valid_row(),
        _valid_row(external_id=9124, company_no="C0008", available_value=0),
    ])
    service = WorkerAuthService(_enabled_settings(), client=client)

    people = service.search_people(" 张_三% ")

    assert people == [{
        "external_id": "9123",
        "company_no": "C0007",
        "group_no": "G0021",
        "display_name": "张三",
    }]
    sql, parameters = client.calls[0]
    assert "[密码]" not in sql
    assert "CONVERT(nvarchar(128), [集团工号]) LIKE ?" in sql
    assert "CONVERT(nvarchar(128), [公司工号]) LIKE ?" in sql
    assert parameters == ("1", "%张[_]三[%]%", "%张[_]三[%]%", "%张[_]三[%]%")


def test_hr_person_lookup_uses_stable_external_identity() -> None:
    client = FakeSqlServerClient([_valid_row()])
    service = WorkerAuthService(_enabled_settings(), client=client)

    person = service.find_person(" 9123 ")

    assert person["company_no"] == "C0007"
    sql, parameters = client.calls[0]
    assert "WHERE [_Identify] = ?" in sql
    assert parameters == ("9123",)
