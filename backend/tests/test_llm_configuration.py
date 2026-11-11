from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.api.auth import require_user
from app.api.configuration import llm_configuration_service, router
from app.configuration_sources import load_environment
from app.main import safe_connection_validation
from app.models.configuration_contracts import LlmCatalogRequest, LlmConfigurationRequest
from app.services import llm_configuration
from app.services.llm_configuration import LlmConfigurationService, ModelCatalogError
from app.services.runtime_settings_store import RuntimeSettingsSaveError, read_runtime_env, write_runtime_env
from tests.line_fixtures import configuration


@pytest.fixture
def service(tmp_path):
    return LlmConfigurationService(configuration(
        tmp_path, llm_enabled=True, llm_base_url="https://provider.invalid/v1",
        llm_api_key="initial-secret", llm_model="old-model",
    ))


def test_save_model_preserves_latest_secret_and_other_settings_until_restart(service):
    path = service.settings.runtime_settings_env_path
    write_runtime_env(path, {"LLM_API_KEY": "latest-${SECRET}", "SMTP_HOST": "mail.invalid"})
    result = service.save(LlmConfigurationRequest(
        enabled=True, base_url="https://provider.invalid/v1/", model="new-model", api_key="",
    ))
    assert result["model"] == "new-model" and result["active_model"] == "old-model"
    assert result["restart_required"] and result["api_key_configured"]
    assert service.settings.llm_model == "old-model"
    assert "latest-" not in str(result)
    values, _, errors = load_environment(service.settings.project_root, {})
    assert not errors
    assert values["LLM_API_KEY"] == "latest-${SECRET}"
    assert values["SMTP_HOST"] == "mail.invalid"
    assert path.stat().st_mode & 0o777 == 0o600
    restarted = LlmConfigurationService(replace(
        service.settings, llm_model=values["LLM_MODEL"], llm_api_key=values["LLM_API_KEY"],
    ))
    assert restarted.configuration()["restart_required"] is False


def test_failed_save_keeps_the_previous_file(service, monkeypatch):
    service.save(LlmConfigurationRequest(enabled=True, base_url=service.settings.llm_base_url, model="first"))
    path = service.settings.runtime_settings_env_path
    before = path.read_bytes()
    monkeypatch.setattr(llm_configuration, "write_runtime_env", lambda *_: (_ for _ in ()).throw(OSError("private details")))
    with pytest.raises(RuntimeSettingsSaveError, match="模型配置保存失败"):
        service.save(LlmConfigurationRequest(enabled=True, base_url=service.settings.llm_base_url, model="second"))
    assert path.read_bytes() == before


def test_selecting_model_keeps_environment_credentials_out_of_saved_overrides(service):
    result = service.save(LlmConfigurationRequest(
        enabled=service.settings.llm_enabled, base_url=service.settings.llm_base_url, model="chosen-model",
    ))
    values = read_runtime_env(service.settings.runtime_settings_env_path)
    assert values == {"LLM_MODEL": "chosen-model"}
    assert result["api_key_configured"] and result["restart_required"]
    assert service.configuration()["base_url"] == service.settings.llm_base_url


def test_catalog_uses_saved_credentials_with_get_and_does_not_save_drafts(service, monkeypatch):
    requested = []
    def respond(request):
        requested.append(request)
        assert request.method == "GET" and request.url.path == "/v1/models"
        assert request.headers["Authorization"] == "Bearer initial-secret"
        return httpx.Response(200, json={"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-b"}]})
    original = httpx.Client
    monkeypatch.setattr(llm_configuration.httpx, "Client", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(respond)))
    assert service.models(LlmCatalogRequest(base_url=service.settings.llm_base_url)) == {"models": ["model-a", "model-b"]}
    assert len(requested) == 1 and not service.settings.runtime_settings_env_path.exists()
    with pytest.raises(ValueError, match="对应接口密钥"):
        service.models(LlmCatalogRequest(base_url="https://another.invalid/v1"))
    assert len(requested) == 1


@pytest.mark.parametrize(("status", "message"), [(401, "认证失败"), (429, "限流"), (404, "手工输入"), (500, "稍后重试")])
def test_catalog_failures_never_include_upstream_body_or_address(service, monkeypatch, status, message):
    original = httpx.Client
    monkeypatch.setattr(llm_configuration.httpx, "Client", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda _: httpx.Response(status, text="sensitive-provider-body")),
    ))
    with pytest.raises(ModelCatalogError) as caught:
        service.models(LlmCatalogRequest(base_url=service.settings.llm_base_url))
    assert message in str(caught.value)
    assert "provider.invalid" not in str(caught.value) and "sensitive" not in str(caught.value)


def test_model_configuration_http_requires_admin_and_redacts_validation(service):
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, safe_connection_validation)
    app.dependency_overrides[llm_configuration_service] = lambda: service
    role = {"role": "admin"}
    app.dependency_overrides[require_user] = lambda: role
    @app.middleware("http")
    async def request_id(request, call_next):
        request.state.request_id = "test-model-config"
        return await call_next(request)
    with TestClient(app) as client:
        result = client.get("/api/v1/configuration/llm")
        assert result.status_code == 200
        assert "initial-secret" not in result.text and "api_key" not in result.json()["data"]
        invalid = client.put("/api/v1/configuration/llm", json={
            "enabled": True, "base_url": "https://provider.invalid/v1", "model": "",
            "api_key": "never-return-this-secret",
        })
        assert invalid.status_code == 422 and "never-return" not in invalid.text
        invalid_url = client.post("/api/v1/configuration/llm/models", json={
            "base_url": "https://user:private-password@provider.invalid/v1", "api_key": "never-return-this-secret",
        })
        assert invalid_url.status_code == 422 and "never-return" not in invalid_url.text and "private-password" not in invalid_url.text
        saved = client.put("/api/v1/configuration/llm", json={
            "enabled": True, "base_url": "https://provider.invalid/v1", "model": "selected",
        })
        assert saved.status_code == 200 and saved.json()["data"]["restart_required"]
        role["role"] = "worker"
        for method, path in [("GET", "/llm"), ("PUT", "/llm"), ("POST", "/llm/models")]:
            assert client.request(method, "/api/v1/configuration" + path, json={}).status_code == 403
    assert read_runtime_env(service.settings.runtime_settings_env_path)["LLM_MODEL"] == "selected"
