"""Compare actual immutable process snapshots with saved targets; never apply them."""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import (
    DEVICE_CATALOG, DEVICE_CONFIG_PATH, LOADED_ENVIRONMENT, LOADED_SOURCES,
    PROCESS_ENVIRONMENT, Settings, _environment,
)
from app.configuration_sources import load_environment
from app.device_config import load_device_catalog
from app.services.automation_runtime import HOT_SETTINGS
from app.services.automation_configuration import AutomationConfigurationRequest
from app.services.database_runtime import DATABASE_FIELDS, DATABASE_DIAGNOSTIC_KEYS
from pydantic import ValidationError


CATEGORIES = ["基础运行配置", "数据库连接配置", "设备及产线配置", "AI 模型配置",
              "报表与后台任务配置", "自动化及 SMTP 配置", "告警与数据保留配置", "文件服务配置"]
ENV_FIELDS = {item.name: item for item in fields(Settings) if "env" in item.metadata}
HOT_FIELDS = {item[0] for item in HOT_SETTINGS.values()}
SOURCE_LABELS = {"default": "程序默认值", "process": "进程环境变量", ".env": ".env",
                 "data/settings.env": "data/settings.env（管理页面保存）", "runtime": "运行时更新",
                 "devices.json": "devices.json", "derived": "运行配置推导"}


def source_label(source: str) -> str:
    if source.endswith("+devices.json"):
        return SOURCE_LABELS.get(source.split("+")[0], "未知") + " + devices.json（未绑定仪表时禁用）"
    return SOURCE_LABELS.get(source, "未知")


def category(key: str) -> str:
    if key in HOT_SETTINGS:
        return CATEGORIES[5]
    if key.startswith(("connections.", "WELD_", "SQLSERVER_", "WORKER_SQLSERVER_", "WORKER_DATABASE_", "FILE_SQLSERVER_", "FILE_DATABASE_", "SQL_WORKBENCH_")):
        return CATEGORIES[1]
    if key.startswith("LLM_"):
        return CATEGORIES[3]
    if "RETENTION" in key or key.startswith("ALARM_"):
        return CATEGORIES[6]
    if key.startswith(("FILE_", "MAGIC_BOX_")):
        return CATEGORIES[7]
    if key.startswith("ENERGY_") or key == "DEVICE_CONFIG_PATH":
        return CATEGORIES[2]
    if key.startswith(("REPORT_", "LEADERSHIP_", "CHART_", "PDF_", "EMAIL_")):
        return CATEGORIES[4]
    return CATEGORIES[0]


def safe_value(value, key=""):
    # Strings may contain URL userinfo, query tokens or authentication material.
    # Text is status-only except for a small set of non-credential formats.
    if isinstance(value, (bool, int)) or isinstance(value, float) and math.isfinite(value):
        return value
    visible = {
        "APP_ENV", "APP_TIMEZONE", "APP_DATA_SOURCE_MODE", "APP_EFFECTIVE_DATA_SOURCE_MODE",
        "ENERGY_GAS_UNIT", "ENERGY_ELECTRICITY_UNIT", "ENERGY_GAS_FACTOR", "ENERGY_ELECTRICITY_FACTOR",
        "ENERGY_SAMPLING_CHANGE_AT", "FILE_SOURCE_MODE",
    }
    if key in visible:
        # Only validated, credential-free formats are displayed as text.
        if isinstance(value, str) and re.fullmatch(r"[\w./:+³-]{1,80}", value):
            return value
    return "已配置" if value else "未配置"


def connection_values(settings: Settings, sources: dict) -> dict:
    result = {}
    for source in ("production", "worker", "file"):
        connection = settings.sqlserver_connection(source)
        primary = source == "production" or connection["uses_primary_fallback"]
        prefix = "SQLSERVER" if primary else f"{source.upper()}_SQLSERVER"
        for name, value in connection.items():
            key = f"connections.{source}.{name}"
            env = f"{prefix}_{name.upper()}" + ("_SECONDS" if name in {"connect_timeout", "query_timeout"} else "")
            if name == "uses_primary_fallback":
                result[key] = (value, "derived", None)
                continue
            if not primary and name in {"driver", "encrypt", "trust_server_certificate"} and not getattr(settings, f"{source}_sqlserver_{name}"):
                env = f"SQLSERVER_{name.upper()}"
            result[key] = (value, sources.get(env, "default"), env)
    mode_key = "APP_DATA_SOURCE_MODE" if settings.data_source_mode else "APP_DEMO_MODE"
    result["APP_EFFECTIVE_DATA_SOURCE_MODE"] = (settings.effective_data_source_mode, sources.get(mode_key, "default"), mode_key)
    return result


def fingerprint(value, key: str) -> str:
    return hmac.new(key.encode(), json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()


def catalog_values(catalog, energy_enabled: bool) -> dict:
    result = {"devices.default_device_id": catalog.default_device_id}
    for line in catalog.production_lines:
        result[f"lines.{line.id}.name"] = line.name
    for device in catalog.devices:
        result[f"devices.{device.id}.energy_api_enabled"] = energy_enabled and device.energy is not None
        for name, value in device.model_dump(mode="json").items():
            if isinstance(value, dict):
                for child, content in value.items():
                    result[f"devices.{device.id}.{name}.{child}"] = content
            else:
                result[f"devices.{device.id}.{name}"] = value
    return result


def validate_values(settings: Settings, values: dict) -> set[str]:
    invalid = set()
    for name, item in ENV_FIELDS.items():
        key = item.metadata["env"]
        value = getattr(settings, name)
        raw = values.get(key)
        if isinstance(value, bool) and raw is not None and raw.strip().lower() not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
            invalid.add(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value) or value < 0 or (key.endswith("_PORT") and not 1 <= value <= 65535):
                invalid.add(key)
    try:
        AutomationConfigurationRequest.model_validate({
            ("smtp_timeout_seconds" if name == "smtp_timeout" else name): getattr(settings, name)
            for name, _ in HOT_SETTINGS.values()
        })
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False):
            name = error["loc"][0] if error["loc"] else None
            invalid.update(key for key, (field, _) in HOT_SETTINGS.items()
                           if name is None or field == name or name == "smtp_timeout_seconds" and field == "smtp_timeout")
    if settings.smtp_use_tls and settings.smtp_use_ssl:
        invalid.update({"SMTP_USE_TLS", "SMTP_USE_SSL"})
    required = []
    if settings.smtp_enabled:
        required.extend([("SMTP_HOST", settings.smtp_host), ("SMTP_FROM_EMAIL", settings.smtp_from_email if "@" in settings.smtp_from_email else "")])
    if settings.llm_enabled:
        required.extend((key, getattr(settings, name)) for key, name in [("LLM_API_KEY", "llm_api_key"), ("LLM_BASE_URL", "llm_base_url"), ("LLM_MODEL", "llm_model")])
    if settings.weld_enabled:
        required.extend((key, getattr(settings, name)) for key, name in [('WELD_DB_HOST', 'weld_db_host'), ('WELD_DB_NAME', 'weld_db_name'), ('WELD_DB_USER', 'weld_db_user'), ('WELD_DB_PASSWORD', 'weld_db_password')])
        try:
            ZoneInfo(settings.weld_db_timezone)
        except (ValueError, ZoneInfoNotFoundError):
            invalid.add('WELD_DB_TIMEZONE')
        for key, valid in [('WELD_DB_POOL_SIZE', 1 <= settings.weld_db_pool_size <= 4),
                           ('WELD_DB_CONNECT_TIMEOUT_SECONDS', 1 <= settings.weld_db_connect_timeout <= 60),
                           ('WELD_DB_QUERY_TIMEOUT_SECONDS', 1 <= settings.weld_db_query_timeout <= 300)]:
            if not valid:
                invalid.add(key)
    for source, enabled in [("production", settings.effective_data_source_mode == "sqlserver"),
                            ("worker", settings.worker_database_auth_enabled), ("file", settings.file_database_enabled)]:
        if enabled:
            connection = settings.sqlserver_connection(source)
            prefix = "SQLSERVER" if source == "production" or connection["uses_primary_fallback"] else f"{source.upper()}_SQLSERVER"
            required.extend((f"{prefix}_{name.upper()}", connection[name]) for name in ("host", "database", "username", "password"))
    invalid.update(key for key, value in required if not value)
    try:
        ZoneInfo(settings.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        invalid.add("APP_TIMEZONE")
    if not 0 <= settings.leadership_report_reconcile_hour <= 23:
        invalid.add("LEADERSHIP_REPORT_RECONCILE_HOUR")
    if settings.file_source_mode not in {"disabled", "local", "http", "mock"}:
        invalid.add("FILE_SOURCE_MODE")
    return invalid


class ConfigurationDiagnostics:
    def __init__(self, settings: Settings, runtime, *, process=None, sources=None, catalog=DEVICE_CATALOG):
        self.settings = settings
        self.database_runtime = None
        self.runtime = runtime
        self.process = dict(PROCESS_ENVIRONMENT if process is None else process)
        self.sources = dict(LOADED_SOURCES if sources is None else sources)
        self.startup_values = dict(LOADED_ENVIRONMENT if process is None else process)
        for name, item in ENV_FIELDS.items():
            value = getattr(settings, name)
            key = item.metadata["env"]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and self.startup_values.get(key) == "":
                self.sources[key] = "default"
        self.catalog = catalog
        self.loaded_at = datetime.now(timezone.utc).isoformat()
        self.version = uuid4().hex[:12]

    def observed(self, comparison_key: str) -> dict:
        hot = self.runtime.inspect()
        database = self.database_runtime.inspect() if self.database_runtime else None
        database_settings = database['settings'] if database else self.settings
        database_sources = {**self.sources, **database['sources']} if database else self.sources
        items = {}
        for name, item in ENV_FIELDS.items():
            key = item.metadata["env"]
            configuration = database_settings if name in DATABASE_FIELDS else hot["settings"] if name in HOT_FIELDS else self.settings
            value = getattr(configuration, name)
            source = database_sources.get(key, "default") if name in DATABASE_FIELDS else hot["sources"].get(name, "default") if name in HOT_FIELDS else self.sources.get(key, "default")
            items[key] = {"value": safe_value(value, key), "source": source, "digest": fingerprint(value, comparison_key)}
        for key, (value, source, _) in connection_values(database_settings, database_sources).items():
            items[key] = {"value": safe_value(value, key), "source": source, "digest": fingerprint(value, comparison_key)}
        items["DEVICE_CONFIG_PATH"] = {"value": "已配置", "source": self.sources.get("DEVICE_CONFIG_PATH", "default"),
                                       "digest": fingerprint(str(DEVICE_CONFIG_PATH), comparison_key)}
        for key, value in catalog_values(self.catalog, self.settings.energy_api_enabled).items():
            source = self.sources.get("ENERGY_API_ENABLED", "default") + "+devices.json" if key.endswith(".energy_api_enabled") else "devices.json"
            items[key] = {"value": value, "source": source, "digest": fingerprint(value, comparison_key)}
        return {"items": items, "loaded_at": self.loaded_at, "hot_loaded_at": max(hot["loaded_at"], database['loaded_at']) if database else hot["loaded_at"],
                "hot_revision": hot["revision"], "error": hot["error"],
                "database_error": database['error'] if database else None,
                "version": f"{self.version}:{hot['revision']}:{database['revision'] if database else 0}"}

    def target(self) -> tuple[dict, set[str], list[str]]:
        values, sources, errors = load_environment(self.settings.project_root, self.process)
        parsed = {}
        invalid = set()
        token = _environment.set(values)
        try:
            for name, item in ENV_FIELDS.items():
                key = item.metadata["env"]
                try:
                    parsed[name] = item.default_factory()
                except (ValueError, TypeError, AttributeError, OverflowError):
                    invalid.add(key)
                    parsed[name] = getattr(self.settings, name)
            try:
                target = replace(self.settings, **parsed)
            except ValueError as exc:
                # Settings validation messages name keys; never return exception text.
                invalid.update(item.metadata["env"] for item in ENV_FIELDS.values() if item.metadata["env"] in str(exc))
                if not invalid:
                    errors.append("目标配置未通过运行参数校验")
                target = self.settings
            invalid.update(validate_values(target, values))
        finally:
            _environment.reset(token)
        targets = {}
        for name, item in ENV_FIELDS.items():
            key = item.metadata["env"]
            source = sources.get(key, "default")
            if isinstance(parsed[name], (int, float)) and not isinstance(parsed[name], bool) and not values.get(key):
                source = "default"
            sources[key] = source
            targets[key] = (parsed[name], source)
        for key, (value, source, dependency) in connection_values(target, sources).items():
            targets[key] = (value, source)
            if dependency in invalid:
                invalid.add(key)
        catalog_path = Path(values.get("DEVICE_CONFIG_PATH", "backend/app/configuration/devices.json")).expanduser()
        if not catalog_path.is_absolute():
            catalog_path = self.settings.project_root / catalog_path
        targets["DEVICE_CONFIG_PATH"] = (str(catalog_path), sources.get("DEVICE_CONFIG_PATH", "default"))
        try:
            catalog = load_device_catalog(catalog_path)
            targets.update({key: (value, sources.get("ENERGY_API_ENABLED", "default") + "+devices.json" if key.endswith(".energy_api_enabled") else "devices.json")
                            for key, value in catalog_values(catalog, parsed["energy_api_enabled"]).items()})
            # Legacy environment variables are assertions, never device overrides.
            from app.config import legacy_device_environment_conflicts
            conflicts = legacy_device_environment_conflicts(catalog, values)
            invalid.update(conflicts)
            if conflicts:
                errors.append("旧设备环境参数与设备目录冲突，请核对并迁入设备目录")
        except (ValueError, OSError):
            invalid.add("DEVICE_CONFIG_PATH")
            errors.append("设备目录无法读取或校验失败")
        return targets, invalid, errors

    def report(self, worker: dict) -> dict:
        comparison_key = worker.get("owner", "web-local-comparison")
        web = self.observed(comparison_key)
        other = worker.get("configuration")
        worker_available = worker.get("state") in {"running", "stalled"} and isinstance(other, dict) and isinstance(other.get("items"), dict)
        targets, invalid, errors = self.target()
        rows = []
        for key in sorted(set(web["items"]) | set(targets) | set(other.get("items", {}) if worker_available else {})):
            target_value, target_source = targets.get(key, (None, "devices.json"))
            desired = fingerprint(target_value, comparison_key)
            database_hot = self.database_runtime is not None and key in DATABASE_DIAGNOSTIC_KEYS
            hot = key in HOT_SETTINGS or database_hot
            processes = {}
            for role, snapshot in [("web", web), ("worker", other if worker_available else None)]:
                item = snapshot.get("items", {}).get(key) if snapshot else None
                matches = item is not None and key in targets and item["digest"] == desired
                abnormal = key in invalid or bool(errors) or not item or bool(snapshot.get("database_error" if database_hot else "error") and hot)
                status = "error" if abnormal else "unconfigured" if matches and item["value"] == "未配置" else "effective" if matches else "pending" if not hot else "error"
                processes[role] = {"value": item["value"] if item else "未知", "source": source_label(item["source"]) if item else "未知",
                                   "matches_saved": matches, "status": status}
            left = web["items"].get(key)
            right = other.get("items", {}).get(key) if worker_available else None
            mismatch = bool(worker_available and (not left or not right or left["digest"] != right["digest"]))
            statuses = {item["status"] for item in processes.values()}
            state = "error" if mismatch or "error" in statuses else "pending" if "pending" in statuses else processes["web"]["status"]
            rows.append({"key": key, "category": CATEGORIES[2] if key.startswith(("devices.", "lines.")) else category(key),
                         "processes": processes, "saved_value": safe_value(target_value, key) if target_source != "devices.json" else target_value,
                         "saved_source": source_label(target_source), "status": state,
                         "valid": key not in invalid and not errors, "process_mismatch": mismatch,
                         "restart_required": not hot and any(item["status"] == "pending" for item in processes.values()),
                         "rule": "保存后新请求和后台操作自动加载；在途操作保留旧配置，无需重启容器" if database_hot else
                         "保存后由各进程下一轮自动化/邮件操作加载；在途操作保留旧快照" if hot else
                         ("数据库声明参数，实际连接及继承来源见 connections.*；" if "SQLSERVER_" in key else "")
                         + "重启 Web 和 Worker；Docker 环境变量变更需重新创建两个容器"})
        return {"categories": CATEGORIES, "items": rows, "errors": errors,
                "precedence": "data/settings.env > 进程环境变量 > .env > 程序默认值；设备业务参数只取 devices.json",
                "processes": {"web": {key: web[key] for key in ("loaded_at", "hot_loaded_at", "hot_revision", "version")},
                              "worker": {"state": worker.get("state", "missing"), "available": worker_available,
                                         **({key: other.get(key) for key in ("loaded_at", "hot_loaded_at", "hot_revision", "version")} if worker_available else {})}},
                "consistent": worker_available and not any(row["process_mismatch"] for row in rows),
                "notes": ["版本号标识各进程加载批次；一致性按实际配置逐项比较，包含凭据变更，不以版本号或脱敏文本判定。","只读快照不触发热更新、数据库连接或后台任务。", "Worker 状态来自独立心跳，缺失或过期时显示异常，不能视为已生效。",
                          "已生效表示与该进程环境下的保存目标一致；宿主机 .env 尚未注入的新值需先重新创建容器。",
                          "数据库专用身份完全留空时沿用生产连接；部分身份已配置时不混用生产身份。"]}
