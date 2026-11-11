from dataclasses import replace
from datetime import date

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
from tests.postgres_fixtures import state_settings


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


def test_model_change_updates_each_process_and_keeps_old_operations_and_caches(postgres_state, service):
    from app.services.application_runtime import build_runtime
    from app.services.database_runtime import database_context
    from app.services.report_analysis_service import PROMPT_VERSION

    settings = state_settings(service.settings, postgres_state)
    runtimes = [build_runtime(settings, role=role) for role in ("web", "worker", "scheduler", "automation")]
    try:
        previous = [database_context(runtime) for runtime in runtimes]
        old = previous[0].lines["Cutter01"].cutting()
        old.metadata.save_analysis(
            cache_key="old-model-cache", report_type="workshop", report_date="2026-11-11",
            input_hash="old-input", data_version="unchanged-data", status="success", mode="model",
            model_name="old-model", prompt_version=PROMPT_VERSION, generated_at="2026-11-11T10:00:00+08:00",
            result=dict(status="success", overview="旧模型结果", features="特征", alerts="关注", suggestions="建议"),
            publish_latest=True,
        )
        old_welding = previous[0].lines["welding"].service
        old_welding.remember_analysis("unchanged-data", "old-welding-analysis")
        app = FastAPI()
        app.state.runtime = runtimes[0]
        app.include_router(router)
        app.dependency_overrides[require_user] = lambda: {"role": "admin"}
        @app.middleware("http")
        async def request_id(request, call_next):
            request.state.request_id = "hot-model-config"
            return await call_next(request)
        with TestClient(app) as client:
            response = client.put("/api/v1/configuration/llm", json={
                "enabled": True, "base_url": settings.llm_base_url, "model": "new-model",
            })
            assert response.status_code == 200
            result = response.json()["data"]
            assert client.get("/api/v1/configuration/llm").json()["data"]["active_model"] == "new-model"
        assert not result["restart_required"] and result["active_model"] == "new-model"
        assert runtimes[1].database_runtime.current_context is previous[1]
        observed = runtimes[1].configuration_diagnostics.observed("test-consumer")
        assert runtimes[1].database_runtime.current_context is previous[1], "diagnostics must not publish configuration"
        pending = runtimes[0].configuration_diagnostics.report({
            "state": "running", "owner": "test-consumer", "configuration": observed,
        })
        row = next(row for row in pending["items"] if row["key"] == "LLM_MODEL")
        assert row["process_mismatch"] and not row["restart_required"]
        for runtime, retained in zip(runtimes, previous):
            updated = database_context(runtime)
            assert updated.settings.llm_model == "new-model"
            assert retained.settings.llm_model == "old-model"
            assert updated.database_sources.capacity is retained.database_sources.capacity
            for key in ("Cutter01", "Cutter02"):
                cutting = updated.lines[key].cutting()
                assert cutting.pipeline.analysis_service.llm.settings.llm_model == "new-model"
                assert cutting.alarm_monitor_service.analyzer.settings.llm_model == "new-model"
                assert retained.lines[key].cutting().pipeline.settings.llm_model == "old-model"
        fresh = database_context(runtimes[0])
        assert fresh.lines["Cutter01"].cutting().report_analysis_service.latest_default_analysis(
            "workshop", date(2026, 11, 11),
        ) is None
        assert old.report_analysis_service.latest_default_analysis("workshop", date(2026, 11, 11)).overview == "旧模型结果"
        assert fresh.lines["welding"].service.cached_analysis("unchanged-data") is None
        assert old_welding.cached_analysis("unchanged-data") == "old-welding-analysis"
        effective = runtimes[0].configuration_diagnostics.report({
            "state": "running", "owner": "test-consumer",
            "configuration": runtimes[1].configuration_diagnostics.observed("test-consumer"),
        })
        assert next(row for row in effective["items"] if row["key"] == "LLM_MODEL")["processes"]["worker"]["matches_saved"]
        current = LlmConfigurationService(fresh.settings, runtimes[0].database_runtime)
        pending_provider = current.save(LlmConfigurationRequest(
            enabled=True, base_url="https://another.invalid/v1", model="provider-new-model",
        ))
        assert pending_provider["restart_required"]
        assert pending_provider["active_model"] == "new-model"
        assert runtimes[0].database_runtime.current_context is fresh
    finally:
        for runtime in reversed(runtimes):
            runtime.database_runtime.close()
            runtime.database.close()


@pytest.mark.parametrize("changes", [
    {"LLM_BASE_URL": "https://another.invalid/v1"},
    {"LLM_API_KEY": "new-provider-secret"}, {"LLM_ENABLED": "false"},
])
def test_pending_provider_configuration_keeps_its_model_until_restart(service, changes):
    from app.services.database_runtime import parse_database_settings
    updated = parse_database_settings(service.settings, {"LLM_MODEL": "provider-new-model", **changes},
                                      current_model="already-hot-model")
    assert updated.llm_model == "already-hot-model"
    assert updated.llm_base_url == service.settings.llm_base_url
    assert updated.llm_api_key == service.settings.llm_api_key


def test_failed_model_write_does_not_publish_the_prepared_services(postgres_state, service, monkeypatch):
    from app.services.application_runtime import build_runtime
    runtime = build_runtime(state_settings(service.settings, postgres_state), role="web")
    try:
        previous = runtime.database_runtime.snapshot()
        current = LlmConfigurationService(previous.settings, runtime.database_runtime)
        monkeypatch.setattr(llm_configuration, "write_runtime_env", lambda *_: (_ for _ in ()).throw(OSError("disk unavailable")))
        with pytest.raises(RuntimeSettingsSaveError, match="模型配置保存失败"):
            current.save(LlmConfigurationRequest(enabled=True, base_url=previous.settings.llm_base_url, model="new-model"))
        assert runtime.database_runtime.current_context is previous
        assert not previous.settings.runtime_settings_env_path.exists()
    finally:
        runtime.database_runtime.close()
        runtime.database.close()
