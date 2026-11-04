"""Compare actual immutable process snapshots with saved targets; never apply them."""
from __future__ import annotations

from dataclasses import fields, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import (
    LOADED_ENVIRONMENT, LOADED_SOURCES,
    PROCESS_ENVIRONMENT, Settings, _environment,
)
from app.configuration_sources import load_environment
from app.device_config import load_line_catalog
from app.services.automation_runtime import AutomationRuntime, HOT_SETTINGS, validate_automation_settings
from app.services.database_runtime import DatabaseRuntime, DATABASE_FIELDS, DATABASE_DIAGNOSTIC_KEYS, validate_sources
from pydantic import ValidationError


CATEGORIES = ["基础运行配置", "数据库连接配置", "设备及产线配置", "AI 模型配置",
              "报表与后台任务配置", "自动化及 SMTP 配置", "告警与数据保留配置", "文件服务配置", "PostgreSQL 应用状态库"]
ENV_FIELDS = {item.name: item for item in fields(Settings) if "env" in item.metadata and item.name != 'consumer_instance_id'}
HOT_FIELDS = {item[0] for item in HOT_SETTINGS.values()}
SOURCE_LABELS = {"default": "程序默认值", "process": "进程环境变量", ".env": ".env",
                 "data/settings.env": "data/settings.env（管理页面保存）", "runtime": "运行时更新",
                 "derived": "运行配置推导"}


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, "未知")


def category(key: str) -> str:
    if key.startswith("POSTGRES_"):
        return CATEGORIES[8]
    if key in HOT_SETTINGS:
        return CATEGORIES[5]
    if key.endswith("_SOURCE_KEY") or key.startswith(("DATABASE_SOURCES_", "connections.", "WELD_", "SQLSERVER_", "WORKER_SQLSERVER_", "WORKER_DATABASE_", "FILE_SQLSERVER_", "FILE_DATABASE_", "SQL_WORKBENCH_")):
        return CATEGORIES[1]
    if key.startswith("LLM_"):
        return CATEGORIES[3]
    if "RETENTION" in key or key.startswith("ALARM_"):
        return CATEGORIES[6]
    if key.startswith(("FILE_", "MAGIC_BOX_")):
        return CATEGORIES[7]
    if key.startswith("ENERGY_") or key == "PRODUCTION_LINES_JSON":
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
        "APP_ENV", "APP_TIMEZONE", "APP_DATA_SOURCE_MODE", "APP_EFFECTIVE_DATA_SOURCE_MODE", "WELD_DB_TIMEZONE",
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
    mode_key = "APP_DATA_SOURCE_MODE" if settings.data_source_mode else "APP_DEMO_MODE"
    result["APP_EFFECTIVE_DATA_SOURCE_MODE"] = (settings.effective_data_source_mode, sources.get(mode_key, "default"), mode_key)
    return result


def fingerprint(value, key: str) -> str:
    return hmac.new(key.encode(), json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode(), hashlib.sha256).hexdigest()


def validate_values(settings: Settings, values: dict) -> set[str]:
    invalid = set()
    from app.database.source_registry import DatabaseSourceRegistry
    try:
        catalog = load_line_catalog(settings.production_lines_json)
    except ValueError:
        invalid.add('PRODUCTION_LINES_JSON')
        catalog = None
    try:
        registry = DatabaseSourceRegistry.from_json(settings.database_sources_json)
        if catalog is not None:
            validate_sources(settings, catalog, registry)
    except ValueError:
        invalid.add('DATABASE_SOURCES_JSON')
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
        validate_automation_settings(settings)
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
    def __init__(self, settings: Settings, runtime: AutomationRuntime, *, process=None, sources=None,
                 database_runtime: DatabaseRuntime | None = None):
        self.settings = settings
        self.database_runtime = database_runtime
        self.runtime = runtime
        self.process = dict(PROCESS_ENVIRONMENT if process is None else process)
        self.sources = dict(LOADED_SOURCES if sources is None else sources)
        self.startup_values = dict(LOADED_ENVIRONMENT if process is None else process)
        for name, item in ENV_FIELDS.items():
            value = getattr(settings, name)
            key = item.metadata["env"]
            if isinstance(value, (int, float)) and not isinstance(value, bool) and self.startup_values.get(key) == "":
                self.sources[key] = "default"
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
        return {"items": items, "loaded_at": self.loaded_at, "hot_loaded_at": max(hot["loaded_at"], database['loaded_at']) if database else hot["loaded_at"],
                "hot_revision": hot["revision"], "error": hot["error"],
                "database_error": database['error'] if database else None,
                "sql_capacity_pending": bool(self.database_runtime and any(item.get('restart_required')
                    for item in self.database_runtime.current_context.database_sources.capacity.status(
                        self.database_runtime.current_context.database_sources.configs())['items'])),
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
        return targets, invalid, errors

    def report(self, worker: dict) -> dict:
        instances = worker.get('consumer_instances', [])
        if instances:
            representative = next((item for item in instances if item.get('state') == 'running'), instances[0])
            worker = {**worker, **representative}
        comparison_key = worker.get("owner", "web-local-comparison")
        web = self.observed(comparison_key)
        other = worker.get("configuration")
        worker_available = worker.get("state") in {"running", "stalled"} and isinstance(other, dict) and isinstance(other.get("items"), dict)
        additional = {}
        for instance in instances:
            if instance.get('state') == 'stopped':
                continue
            remote = instance.get('configuration')
            available = instance.get('state') in {'running', 'stalled'} and isinstance(remote, dict) and isinstance(remote.get('items'), dict)
            additional['consumer:' + instance['instance_id']] = (
                instance, remote if available else None, self.observed(instance['owner']))
        for role in ('scheduler', 'automation'):
            status = worker.get('roles', {}).get(role)
            if status is not None:
                remote = status.get('configuration')
                available = status.get('state') in {'running','stalled'} and isinstance(remote,dict) and isinstance(remote.get('items'),dict)
                additional[role] = (status, remote if available else None, self.observed(status.get('owner','unavailable')))
        targets, invalid, errors = self.target()
        rows = []
        for key in sorted(set(web["items"]) | set(targets) | set(other.get("items", {}) if worker_available else {})):
            target_value, target_source = targets.get(key, (None, "default"))
            desired = fingerprint(target_value, comparison_key)
            database_hot = self.database_runtime is not None and key in DATABASE_DIAGNOSTIC_KEYS
            hot = key in HOT_SETTINGS or database_hot
            processes = {}
            for role, snapshot in [("web", web), ("worker", other if worker_available else None)]:
                item = snapshot.get("items", {}).get(key) if snapshot else None
                matches = item is not None and key in targets and item["digest"] == desired
                capacity_pending = bool(key == 'DATABASE_SOURCES_JSON' and snapshot and snapshot.get('sql_capacity_pending'))
                abnormal = key in invalid or bool(errors) or not item or bool(snapshot.get("database_error" if database_hot else "error") and hot)
                status = "error" if abnormal else "unconfigured" if matches and item["value"] == "未配置" else "effective" if matches else "pending" if not hot else "error"
                if capacity_pending and not abnormal:
                    status = 'pending'
                processes[role] = {"value": item["value"] if item else "未知", "source": source_label(item["source"]) if item else "未知",
                                   "matches_saved": matches, "status": status}
            left = web["items"].get(key)
            right = other.get("items", {}).get(key) if worker_available else None
            mismatch = bool(worker_available and (not left or not right or left["digest"] != right["digest"]))
            for role, (status, remote, local) in additional.items():
                item = remote.get('items', {}).get(key) if remote else None
                reference = local['items'].get(key)
                matches = bool(item and key in targets and item['digest']==fingerprint(target_value,status.get('owner','unavailable')))
                changed = bool(item and (not reference or item['digest'] != reference['digest']))
                pending = bool(remote and key=='DATABASE_SOURCES_JSON' and remote.get('sql_capacity_pending'))
                state = 'error' if key in invalid or errors or not item or remote.get('database_error' if database_hot else 'error') else 'pending' if pending or not matches and not hot else 'error' if not matches else 'unconfigured' if item['value']=='未配置' else 'effective'
                processes[role] = {'value':item['value'] if item else '未知','source':source_label(item['source']) if item else '未知','matches_saved':matches,'status':state}
                mismatch = mismatch or changed
            statuses = {item["status"] for item in processes.values()}
            state = "error" if mismatch or "error" in statuses else "pending" if "pending" in statuses else processes["web"]["status"]
            processes['consumers'] = {role[9:]: processes.pop(role) for role in list(processes) if role.startswith('consumer:')}
            rows.append({"key": key, "category": CATEGORIES[2] if key.startswith(("devices.", "lines.")) else category(key),
                         "processes": processes, "saved_value": safe_value(target_value, key),
                         "saved_source": source_label(target_source), "status": state,
                         "valid": key not in invalid and not errors, "process_mismatch": mismatch,
                         "restart_required": (not hot or key == 'DATABASE_SOURCES_JSON') and 'pending' in statuses,
                         "rule": "连接参数按新操作热加载；sql_max_concurrent 重启 Web/Scheduler/Consumer/Automation 生效，实际容量见数据库资源诊断" if key == 'DATABASE_SOURCES_JSON' else
                         "保存后新请求和后台操作自动加载；在途操作保留旧配置，无需重启容器" if database_hot else
                         "保存后由各进程下一轮自动化/邮件操作加载；在途操作保留旧快照" if hot else
                         ("数据库声明参数，实际连接及继承来源见 connections.*；" if "SQLSERVER_" in key else "")
                         + "重启 Web、Scheduler、Consumer 和 Automation；Docker 环境变量变更需重新创建四个应用容器"})
        versions = {role:{'state':status.get('state','missing'),'available':remote is not None,
                         **({key:remote.get(key) for key in ('loaded_at','hot_loaded_at','hot_revision','version')} if remote else {})}
                    for role,(status,remote,local) in additional.items()}
        consumer_versions = {role[9:]: versions.pop(role) for role in list(versions) if role.startswith('consumer:')}
        consumer_versions.update({item['instance_id']: {'state':'stopped', 'available':False}
                                  for item in instances if item.get('state') == 'stopped'})
        return {"categories": CATEGORIES, "items": rows, "errors": errors,
                "precedence": "data/settings.env > 进程环境变量 > .env > 程序默认值；产线与连接只取统一配置",
                "processes": {**versions, 'consumers': consumer_versions, "web": {key: web[key] for key in ("loaded_at", "hot_loaded_at", "hot_revision", "version")},
                              "worker": {"state": worker.get("state", "missing"), "available": worker_available,
                                         **({key: other.get(key) for key in ("loaded_at", "hot_loaded_at", "hot_revision", "version")} if worker_available else {})}},
                "consistent": worker_available and all(remote is not None for _,remote,_ in additional.values()) and not any(row["process_mismatch"] for row in rows),
                "notes": ["版本号标识各进程加载批次；一致性按实际配置逐项比较，包含凭据变更，不以版本号或脱敏文本判定。","只读快照不触发热更新、数据库连接或后台任务。", "Worker 状态来自独立心跳，缺失或过期时显示异常，不能视为已生效。",
                          "已生效表示与该进程环境下的保存目标一致；宿主机 .env 尚未注入的新值需先重新创建容器。",
                          "每条产线及共享业务通过显式 source_key 绑定数据源，不继承其他产线连接。"]}
