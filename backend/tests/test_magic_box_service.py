from dataclasses import replace
import hashlib
from pathlib import Path

import httpx

from app.config import settings
from app.services.magic_box_service import (
    MagicBoxClient,
    MagicBoxConfig,
    MagicBoxConfigProvider,
)


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
