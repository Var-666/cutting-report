"""Read model catalogs and save model settings for the next process start."""
from __future__ import annotations

import json

import httpx

from app.config import Settings
from app.core.process_lock import settings_file_lock
from app.models.configuration_contracts import LlmCatalogRequest, LlmConfigurationRequest
from app.services.runtime_settings_store import (
    RuntimeSettingsSaveError, read_runtime_env, runtime_settings_lock, write_runtime_env,
)


class ModelCatalogError(RuntimeError):
    pass


class LlmConfigurationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _saved(self) -> LlmConfigurationRequest:
        try:
            saved = read_runtime_env(self.settings.runtime_settings_env_path)
            return LlmConfigurationRequest(
                enabled=saved.get("LLM_ENABLED", self.settings.llm_enabled),
                base_url=saved.get("LLM_BASE_URL", self.settings.llm_base_url),
                model=saved.get("LLM_MODEL", self.settings.llm_model),
                api_key=saved.get("LLM_API_KEY") or self.settings.llm_api_key,
            )
        except (OSError, ValueError):
            raise RuntimeSettingsSaveError("模型配置无法读取，请检查运行配置文件") from None

    def _view(self, saved: LlmConfigurationRequest) -> dict:
        key = saved.api_key.get_secret_value() if saved.api_key is not None else ""
        current = self.settings
        return dict(
            enabled=saved.enabled, base_url=saved.base_url, model=saved.model,
            api_key_configured=bool(key), active_enabled=current.llm_enabled,
            active_base_url=current.llm_base_url, active_model=current.llm_model,
            restart_required=(saved.enabled, saved.base_url, saved.model, key) != (
                current.llm_enabled, current.llm_base_url.rstrip("/"), current.llm_model, current.llm_api_key),
        )

    def configuration(self) -> dict:
        return self._view(self._saved())

    def save(self, payload: LlmConfigurationRequest) -> dict:
        if payload.enabled and (not payload.base_url or not payload.model):
            raise ValueError("启用智能分析时必须填写接口地址和模型 ID")
        path = self.settings.runtime_settings_env_path
        with runtime_settings_lock, settings_file_lock(path):
            saved = self._saved()
            submitted_key = payload.api_key.get_secret_value() if payload.api_key is not None else ""
            key = submitted_key or (saved.api_key.get_secret_value() if saved.api_key is not None else "")
            if payload.enabled and not key:
                raise ValueError("启用智能分析时必须配置接口密钥")
            candidate = payload.model_copy(update={"api_key": saved.api_key}) if not submitted_key else payload
            values = {"LLM_MODEL": candidate.model}
            if candidate.enabled != saved.enabled:
                values["LLM_ENABLED"] = str(candidate.enabled).lower()
            if candidate.base_url != saved.base_url:
                values["LLM_BASE_URL"] = candidate.base_url
            if submitted_key:
                values["LLM_API_KEY"] = submitted_key
            try:
                write_runtime_env(path, values)
            except (OSError, ValueError):
                raise RuntimeSettingsSaveError("模型配置保存失败") from None
            return self._view(candidate)

    def models(self, payload: LlmCatalogRequest) -> dict:
        if not payload.base_url:
            raise ValueError("请先填写模型接口地址")
        saved = self._saved()
        key = payload.api_key.get_secret_value() if payload.api_key is not None else ""
        if not key:
            if payload.base_url != saved.base_url:
                raise ValueError("读取新接口的模型列表时，请填写对应接口密钥")
            key = saved.api_key.get_secret_value() if saved.api_key is not None else ""
        if not key:
            raise ValueError("请先填写模型接口密钥")
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                with client.stream("GET", payload.base_url + "/models", headers={"Authorization": "Bearer " + key}) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 1_048_576:
                            raise ModelCatalogError("模型列表超过容量限制，请手工输入模型 ID")
            body = json.loads(content)
            rows = body.get("data") if isinstance(body, dict) else None
            if not isinstance(rows, list) or any(
                not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows
            ):
                raise ValueError("invalid catalog")
            models = {
                LlmConfigurationRequest.validate_model(row["id"]) for row in rows
                if row["id"].strip() and len(row["id"]) <= 256
            }
            return {"models": sorted(models)}
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in {401, 403}:
                message = "读取模型列表认证失败，请检查对应接口密钥和权限"
            elif status == 429:
                message = "模型接口限流，请稍后读取模型列表"
            elif status in {404, 405}:
                message = f"接口暂不支持读取模型列表（HTTP {status}），可手工输入模型 ID"
            else:
                message = f"读取模型列表失败（HTTP {status}），请稍后重试"
            raise ModelCatalogError(message) from None
        except httpx.TimeoutException:
            raise ModelCatalogError("读取模型列表超时，请稍后重试") from None
        except httpx.HTTPError:
            raise ModelCatalogError("无法连接模型接口，请检查接口地址和网络") from None
        except ValueError:
            raise ModelCatalogError("模型列表格式无法识别，可手工输入模型 ID") from None
