from dataclasses import replace
import hashlib
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.services.magic_box_service import (
    MagicBoxClient,
    MagicBoxConfig,
    MagicBoxConfigProvider,
    MagicBoxError,
)
from app.services.report_deadline import operation_deadline


class ConfigRows:
    def fetch_all(self, sql, parameters):
        assert "dbo.SYS_CONFIG" in sql
        assert tuple(parameters) == (
            "CUTTER_GATEWAY", "CUTTER_TOKEN", "CUTTER_SECRET",
            "CUTTER01_IP", "CUTTER02_IP",
        )
        return [
            {"CONFIG_NAME": "CUTTER_GATEWAY", "CONFIG_VALUE": "http://10.147.1.41:8080"},
            {"CONFIG_NAME": "CUTTER_TOKEN", "CONFIG_VALUE": "token-value"},
            {"CONFIG_NAME": "CUTTER_SECRET", "CONFIG_VALUE": "secret-value"},
            {"CONFIG_NAME": "CUTTER01_IP", "CONFIG_VALUE": "10.147.1.40"},
            {"CONFIG_NAME": "CUTTER02_IP", "CONFIG_VALUE": "10.147.1.38"},
        ]


def test_config_provider_reads_whitelist_and_hides_secrets():
    cfg = replace(settings, magic_box_enabled=True)
    provider = MagicBoxConfigProvider(cfg, client=ConfigRows(), clock=lambda: 1)

    config = provider.get()
    public = provider.public_status()

    assert config.machine_ips == ("10.147.1.40", "10.147.1.38")
    assert public["configured"] is True
    assert "token-value" not in repr(public)
    assert "secret-value" not in repr(public)


def test_upload_uses_actual_filename_for_task_name_and_signed_headers(tmp_path: Path):
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        if request.url.path == "/api/task/upload":
            return httpx.Response(200, json={"status": 0, "data": "remote-uuid"})
        if request.url.path == "/api/task/assign":
            return httpx.Response(200, json={"status": 0, "data": None})
        raise AssertionError(request.url.path)

    path = tmp_path / "PANEL-001.GEN"
    path.write_bytes(b"GEN-CONTENT")
    client = MagicBoxClient(
        MagicBoxConfig("http://magic.internal:8080", "token", "secret", ("10.1.1.2",)),
        transport=httpx.MockTransport(handler),
        clock_ms=lambda: 1000,
    )

    assert client.upload_task(
        task_identifier="dsp_unique",
        task_name=path.name,
        file_path=path,
        material="AH36",
        thickness=12,
        count=1,
    ) == "remote-uuid"
    client.assign_task(task_identifier="dsp_unique", machine_ip="10.1.1.2")

    upload = requests[0]
    body = upload.content
    assert b'name="taskName"' in body and b"PANEL-001.GEN" in body
    assert b'name="taskFile"; filename="PANEL-001.GEN"' in body
    assert b'name="taskIdentifier"' in body and b"dsp_unique" in body
    assert upload.headers["timestamp"] == "1000"
    expected = hashlib.md5(
        b"timestamp=1000&token=token&secret=secret", usedforsecurity=False
    ).hexdigest()
    assert upload.headers["sign"] == expected
    assert requests[1].headers["timestamp"] == "1001"
    assert requests[1].headers.get("secret") is None


@pytest.mark.parametrize("fault", ["timeout", "disconnect", "non_json", "array",
    "server_error", "missing_status", "boolean_status", "missing_uuid", "object_uuid"])
def test_uncertain_upload_never_becomes_a_safe_retry(tmp_path, fault):
    def handler(request):
        if fault == "timeout":
            raise httpx.ReadTimeout("lost response", request=request)
        if fault == "disconnect":
            raise httpx.ReadError("connection lost", request=request)
        if fault == "non_json":
            return httpx.Response(200, text="proxy response")
        if fault == "array":
            return httpx.Response(200, json=[])
        if fault == "server_error":
            return httpx.Response(503, json={"status": 1})
        if fault == "missing_status":
            return httpx.Response(200, json={"data": "uuid"})
        if fault == "boolean_status":
            return httpx.Response(200, json={"status": False, "data": "uuid"})
        return httpx.Response(200, json={"status": 0,
            "data": {} if fault == "object_uuid" else None})

    path = tmp_path / "cut.GEN"
    path.write_bytes(b"cut")
    client = MagicBoxClient(MagicBoxConfig("http://magic.internal", "t", "s"),
        transport=httpx.MockTransport(handler))
    with pytest.raises(MagicBoxError) as error:
        client.upload_task(task_identifier="same-id", task_name=path.name,
            file_path=path, material=None, thickness=None, count=1)
    assert error.value.outcome_unknown is True


@pytest.mark.parametrize("operation", ["upload", "assign", "cancel"])
def test_explicit_business_rejection_is_safe_but_transport_failure_is_unknown(tmp_path, operation):
    path = tmp_path / "cut.GEN"
    path.write_bytes(b"cut")
    for status, unknown in [(200, False), (500, True)]:
        client = MagicBoxClient(MagicBoxConfig("http://magic.internal", "t", "s"),
            transport=httpx.MockTransport(lambda _: httpx.Response(status,
                json={"status": 1001, "msg": "rejected"})))
        with pytest.raises(MagicBoxError) as error:
            if operation == "upload":
                client.upload_task(task_identifier="id", task_name=path.name,
                    file_path=path, material=None, thickness=None, count=1)
            elif operation == "assign":
                client.assign_task(task_identifier="id", machine_ip="10.1.1.1")
            else:
                client.cancel_assignment(task_identifier="id")
        assert error.value.outcome_unknown is unknown


def test_unreadable_file_before_request_is_safe(tmp_path):
    client = MagicBoxClient(MagicBoxConfig("http://magic.internal", "t", "s"),
        transport=httpx.MockTransport(lambda _: pytest.fail("must not send")))
    with pytest.raises(MagicBoxError) as error:
        client.upload_task(task_identifier="id", task_name="missing.GEN",
            file_path=tmp_path / "missing.GEN", material=None, thickness=None, count=1)
    assert error.value.outcome_unknown is False


def test_request_timeout_is_capped_by_shared_operation_deadline(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def request(self, method, path, **kwargs):
            return httpx.Response(200, json={"status": 0, "data": []})

    monkeypatch.setattr("app.services.magic_box_service.httpx.Client", FakeClient)
    client = MagicBoxClient(
        MagicBoxConfig("http://magic.internal", "token", "secret"), timeout=60
    )

    with operation_deadline(5):
        assert client.list_machines() == []

    assert 0 < captured["timeout"] <= 5


def test_operation_deadline_is_reported_as_magic_box_timeout():
    client = MagicBoxClient(
        MagicBoxConfig("http://magic.internal", "token", "secret"), timeout=60
    )

    with operation_deadline(0), pytest.raises(MagicBoxError) as error:
        client.list_machines()

    assert error.value.status_code == 504
    assert error.value.outcome_unknown is False
