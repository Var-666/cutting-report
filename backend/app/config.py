from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.configuration_sources import load_environment

from app.device_config import load_device_catalog


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SETTINGS_ENV_PATH = PROJECT_ROOT / "data" / "settings.env"
PROCESS_ENVIRONMENT = dict(os.environ)
# Keep the deployed precedence, including settings.env overriding process variables.
LOADED_ENVIRONMENT, LOADED_SOURCES, LOAD_ERRORS = load_environment(PROJECT_ROOT, PROCESS_ENVIRONMENT)
os.environ.update(LOADED_ENVIRONMENT)
_environment = ContextVar("configuration_environment", default=LOADED_ENVIRONMENT)


def configuration_field(key, factory, *, hidden=False):
    hidden = hidden or any(part in key for part in ("PASSWORD", "API_KEY", "TOKEN", "AUTHORIZATION", "COOKIE", "USERNAME"))
    return field(default_factory=factory, metadata={"env": key}, repr=not hidden)


# Device business settings come only from the catalog; credentials stay in env.
DEVICE_CONFIG_PATH = Path(os.getenv("DEVICE_CONFIG_PATH", "backend/app/configuration/devices.json")).expanduser()
if not DEVICE_CONFIG_PATH.is_absolute():
    DEVICE_CONFIG_PATH = PROJECT_ROOT / DEVICE_CONFIG_PATH
DEVICE_CATALOG = load_device_catalog(DEVICE_CONFIG_PATH)
DEFAULT_DEVICE = DEVICE_CATALOG.default_device


def legacy_device_environment_conflicts(catalog, environment) -> dict[str, str]:
    """Legacy variables assert catalog compatibility; they never override devices."""
    device = catalog.default_device
    energy = device.energy
    expected = {
        "REPORT_MACHINE_NO": device.machine_no,
        "ENERGY_EQUIPMENT_IDS": ",".join(energy.equipment_ids) if energy else "",
        "ENERGY_REPORT_EQUIPMENT_IDS": ",".join(energy.equipment_ids) if energy else device.machine_no,
        "ENERGY_GAS_PARAMETER_ID": str(energy.gas_parameter_id) if energy else "",
        "ENERGY_ELECTRICITY_PARAMETER_IDS": ",".join(map(str, energy.electricity_parameter_ids)) if energy else "",
        "ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS": ",".join(map(str, energy.electricity_parameter_ids)) if energy else "",
        "ALARM_MONITOR_MACHINE_NOS": device.machine_no,
        "ALARM_MONITOR_START_AT": device.alarm.start_at,
    }
    conflicts = {}
    for key, value in expected.items():
        actual = environment.get(key, "").strip()
        if actual and tuple(part.strip() for part in actual.split(",")) != tuple(value.split(",")):
            conflicts[key] = f"{key}: 环境={actual!r}，目录={value!r}"
    return conflicts


def validate_legacy_device_environment() -> None:
    conflicts = legacy_device_environment_conflicts(DEVICE_CATALOG, LOADED_ENVIRONMENT)
    if conflicts:
        raise ValueError("旧设备环境配置与设备目录冲突；请核对并迁入设备目录后移除旧变量：" + "; ".join(conflicts.values()))


validate_legacy_device_environment()


def _as_bool(name: str, default: bool) -> bool:
    value = _environment.get().get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    value = _environment.get().get(name)
    return int(value) if value else default


def _as_float(name: str, default: float) -> float:
    value = _environment.get().get(name)
    return float(value) if value else default


@dataclass(frozen=True, slots=True)
class Settings:
    device_id: str = DEFAULT_DEVICE.id
    device_name: str = DEFAULT_DEVICE.name
    production_line_id: str = DEFAULT_DEVICE.production_line_id
    production_data_source: str = DEFAULT_DEVICE.data_source
    energy_connected: bool = DEFAULT_DEVICE.energy is not None
    project_root: Path = PROJECT_ROOT
    app_env: str = configuration_field("APP_ENV", lambda: _environment.get().get("APP_ENV", "development"))
    app_host: str = configuration_field("APP_HOST", lambda: _environment.get().get("APP_HOST", "127.0.0.1"))
    app_port: int = configuration_field("APP_PORT", lambda: _as_int("APP_PORT", 8000))
    timezone: str = configuration_field("APP_TIMEZONE", lambda: _environment.get().get("APP_TIMEZONE", "Asia/Shanghai"))
    app_version: str = configuration_field("APP_VERSION", lambda: _environment.get().get("APP_VERSION", "0.1.0"))
    demo_mode: bool = configuration_field("APP_DEMO_MODE", lambda: _as_bool("APP_DEMO_MODE", True))
    data_source_mode: str = configuration_field("APP_DATA_SOURCE_MODE", lambda: _environment.get().get("APP_DATA_SOURCE_MODE", "").strip().lower())
    cors_origins: tuple[str, ...] = configuration_field("APP_CORS_ORIGINS", lambda: tuple(
        item.strip()
        for item in _environment.get().get("APP_CORS_ORIGINS", "http://localhost:5173").split(",")
        if item.strip()
    ))

    sqlserver_host: str = configuration_field("SQLSERVER_HOST", lambda: _environment.get().get("SQLSERVER_HOST", ""))
    sqlserver_port: int = configuration_field("SQLSERVER_PORT", lambda: _as_int("SQLSERVER_PORT", 1433))
    sqlserver_database: str = configuration_field("SQLSERVER_DATABASE", lambda: _environment.get().get("SQLSERVER_DATABASE", ""))
    sqlserver_username: str = configuration_field("SQLSERVER_USERNAME", lambda: _environment.get().get("SQLSERVER_USERNAME", ""))
    sqlserver_password: str = configuration_field("SQLSERVER_PASSWORD", lambda: _environment.get().get("SQLSERVER_PASSWORD", ""))
    sqlserver_driver: str = configuration_field("SQLSERVER_DRIVER", lambda: _environment.get().get("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server"))
    sqlserver_encrypt: str = configuration_field("SQLSERVER_ENCRYPT", lambda: _environment.get().get("SQLSERVER_ENCRYPT", "yes"))
    sqlserver_trust_server_certificate: str = configuration_field("SQLSERVER_TRUST_SERVER_CERTIFICATE", lambda: _environment.get().get(
        "SQLSERVER_TRUST_SERVER_CERTIFICATE", "yes"
    ))
    sqlserver_connect_timeout: int = configuration_field("SQLSERVER_CONNECT_TIMEOUT_SECONDS", lambda: _as_int("SQLSERVER_CONNECT_TIMEOUT_SECONDS", 5))
    sqlserver_query_timeout: int = configuration_field("SQLSERVER_QUERY_TIMEOUT_SECONDS", lambda: _as_int("SQLSERVER_QUERY_TIMEOUT_SECONDS", 120))
    leadership_report_cache_seconds: int = configuration_field("LEADERSHIP_REPORT_CACHE_SECONDS", lambda: _as_int(
        "LEADERSHIP_REPORT_CACHE_SECONDS", 600
    ))
    leadership_report_history_cache_seconds: int = configuration_field("LEADERSHIP_REPORT_HISTORY_CACHE_SECONDS", lambda: _as_int(
        "LEADERSHIP_REPORT_HISTORY_CACHE_SECONDS", 86400
    ))
    leadership_report_reconcile_hour: int = configuration_field("LEADERSHIP_REPORT_RECONCILE_HOUR", lambda: _as_int(
        "LEADERSHIP_REPORT_RECONCILE_HOUR", 2
    ))
    report_data_cache_seconds: int = configuration_field("REPORT_DATA_CACHE_SECONDS", lambda: _as_int("REPORT_DATA_CACHE_SECONDS", 600))
    report_background_refresh_seconds: int = configuration_field("REPORT_BACKGROUND_REFRESH_SECONDS", lambda: _as_int("REPORT_BACKGROUND_REFRESH_SECONDS", 300))
    report_machine_no: str = DEFAULT_DEVICE.machine_no

    weld_enabled: bool = configuration_field("WELD_ENABLED", lambda: _as_bool("WELD_ENABLED", False))
    weld_db_host: str = configuration_field("WELD_DB_HOST", lambda: _environment.get().get("WELD_DB_HOST", ""))
    weld_db_port: int = configuration_field("WELD_DB_PORT", lambda: _as_int("WELD_DB_PORT", 3306))
    weld_db_name: str = configuration_field("WELD_DB_NAME", lambda: _environment.get().get("WELD_DB_NAME", "weld_production_data"))
    weld_db_user: str = configuration_field("WELD_DB_USER", lambda: _environment.get().get("WELD_DB_USER", ""), hidden=True)
    weld_db_password: str = configuration_field("WELD_DB_PASSWORD", lambda: _environment.get().get("WELD_DB_PASSWORD", ""))
    weld_db_timezone: str = configuration_field("WELD_DB_TIMEZONE", lambda: _environment.get().get("WELD_DB_TIMEZONE", "Asia/Shanghai"))
    weld_db_pool_size: int = configuration_field("WELD_DB_POOL_SIZE", lambda: _as_int("WELD_DB_POOL_SIZE", 2))
    weld_db_connect_timeout: int = configuration_field("WELD_DB_CONNECT_TIMEOUT_SECONDS", lambda: _as_int("WELD_DB_CONNECT_TIMEOUT_SECONDS", 5))
    weld_db_query_timeout: int = configuration_field("WELD_DB_QUERY_TIMEOUT_SECONDS", lambda: _as_int("WELD_DB_QUERY_TIMEOUT_SECONDS", 120))

    # 能管平台只读接入。仪表绑定、计量边界和单位必须由现场确认。
    energy_api_enabled: bool = configuration_field("ENERGY_API_ENABLED", lambda: _as_bool("ENERGY_API_ENABLED", False))
    energy_api_base_url: str = configuration_field("ENERGY_API_BASE_URL", lambda: _environment.get().get("ENERGY_API_BASE_URL", "http://10.147.0.38:10091"))
    energy_api_username: str = configuration_field("ENERGY_API_USERNAME", lambda: _environment.get().get("ENERGY_API_USERNAME", "").strip())
    energy_api_password: str = configuration_field("ENERGY_API_PASSWORD", lambda: _environment.get().get("ENERGY_API_PASSWORD", ""), hidden=True)
    energy_api_timeout: float = configuration_field("ENERGY_API_TIMEOUT_SECONDS", lambda: _as_float("ENERGY_API_TIMEOUT_SECONDS", 10))
    energy_cache_seconds: int = configuration_field("ENERGY_CACHE_SECONDS", lambda: _as_int("ENERGY_CACHE_SECONDS", 60))
    energy_equipment_ids: tuple[str, ...] = ()
    energy_gas_parameter_id: str = str(DEFAULT_DEVICE.energy.gas_parameter_id) if DEFAULT_DEVICE.energy else ""
    energy_electricity_parameter_ids: tuple[str, ...] = ()
    energy_gas_unit: str = configuration_field("ENERGY_GAS_UNIT", lambda: _environment.get().get("ENERGY_GAS_UNIT", "Nm³").strip())
    energy_electricity_unit: str = configuration_field("ENERGY_ELECTRICITY_UNIT", lambda: _environment.get().get("ENERGY_ELECTRICITY_UNIT", "kWh").strip())
    # 平台 Mv 已乘读数因子；以下仅用于经确认的单位转换，禁止再次乘 CT/PT。
    energy_gas_factor: str = configuration_field("ENERGY_GAS_FACTOR", lambda: _environment.get().get("ENERGY_GAS_FACTOR", "1"))
    energy_electricity_factor: str = configuration_field("ENERGY_ELECTRICITY_FACTOR", lambda: _environment.get().get("ENERGY_ELECTRICITY_FACTOR", "1"))
    energy_max_gap_seconds: int = configuration_field("ENERGY_MAX_GAP_SECONDS", lambda: _as_int("ENERGY_MAX_GAP_SECONDS", 300))
    energy_allow_interpolation: bool = configuration_field("ENERGY_ALLOW_INTERPOLATION", lambda: _as_bool("ENERGY_ALLOW_INTERPOLATION", True))
    # 留空不启用新规则；时间按 APP_TIMEZONE 解释，也可显式附带时区。
    energy_sampling_change_at: str = configuration_field("ENERGY_SAMPLING_CHANGE_AT", lambda: _environment.get().get(
        "ENERGY_SAMPLING_CHANGE_AT", "2026-09-04T00:00:00+08:00"
    ).strip())
    energy_new_max_gap_seconds: int = configuration_field("ENERGY_NEW_MAX_GAP_SECONDS", lambda: _as_int("ENERGY_NEW_MAX_GAP_SECONDS", 180))

    # 统一设备能耗报表：当前仪表组只归属一台物理设备。多个值仅用于
    # 同一设备在 TASK.MACHINE_NO 中的历史别名，不能混入另一台设备。
    energy_report_equipment_ids: tuple[str, ...] = DEFAULT_DEVICE.energy.equipment_ids if DEFAULT_DEVICE.energy else (DEFAULT_DEVICE.machine_no,)
    energy_report_electricity_parameter_ids: tuple[str, ...] = tuple(map(str, DEFAULT_DEVICE.energy.electricity_parameter_ids)) if DEFAULT_DEVICE.energy else ()
    energy_report_max_gap_seconds: int = configuration_field("ENERGY_REPORT_MAX_GAP_SECONDS", lambda: _as_int("ENERGY_REPORT_MAX_GAP_SECONDS", 1800))
    energy_report_snapshot_ttl_seconds: int = configuration_field("ENERGY_REPORT_SNAPSHOT_TTL_SECONDS", lambda: _as_int("ENERGY_REPORT_SNAPSHOT_TTL_SECONDS", 3600))

    # HR 工人库与生产任务库可以位于不同的 SQL Server 实例。
    # 专用地址、库名、账号或密码任意一项被配置后，该数据源就不再
    # 混用生产库的连接字段；旧部署在完全未配置时仍可显式回退。
    worker_sqlserver_host: str = configuration_field("WORKER_SQLSERVER_HOST", lambda: _environment.get().get("WORKER_SQLSERVER_HOST", ""))
    worker_sqlserver_port: int = configuration_field("WORKER_SQLSERVER_PORT", lambda: _as_int("WORKER_SQLSERVER_PORT", 1433))
    worker_sqlserver_database: str = configuration_field("WORKER_SQLSERVER_DATABASE", lambda: _environment.get().get("WORKER_SQLSERVER_DATABASE", ""))
    worker_sqlserver_username: str = configuration_field("WORKER_SQLSERVER_USERNAME", lambda: _environment.get().get("WORKER_SQLSERVER_USERNAME", ""))
    worker_sqlserver_password: str = configuration_field("WORKER_SQLSERVER_PASSWORD", lambda: _environment.get().get("WORKER_SQLSERVER_PASSWORD", ""))
    worker_sqlserver_driver: str = configuration_field("WORKER_SQLSERVER_DRIVER", lambda: _environment.get().get("WORKER_SQLSERVER_DRIVER", ""))
    worker_sqlserver_encrypt: str = configuration_field("WORKER_SQLSERVER_ENCRYPT", lambda: _environment.get().get("WORKER_SQLSERVER_ENCRYPT", ""))
    worker_sqlserver_trust_server_certificate: str = configuration_field("WORKER_SQLSERVER_TRUST_SERVER_CERTIFICATE", lambda: _environment.get().get(
        "WORKER_SQLSERVER_TRUST_SERVER_CERTIFICATE", ""
    ))
    worker_sqlserver_connect_timeout: int = configuration_field("WORKER_SQLSERVER_CONNECT_TIMEOUT_SECONDS", lambda: _as_int(
        "WORKER_SQLSERVER_CONNECT_TIMEOUT_SECONDS", 5
    ))
    worker_sqlserver_query_timeout: int = configuration_field("WORKER_SQLSERVER_QUERY_TIMEOUT_SECONDS", lambda: _as_int(
        "WORKER_SQLSERVER_QUERY_TIMEOUT_SECONDS", 15
    ))

    worker_database_auth_enabled: bool = configuration_field("WORKER_DATABASE_AUTH_ENABLED", lambda: _as_bool(
        "WORKER_DATABASE_AUTH_ENABLED", False
    ))
    worker_database_session_check_seconds: float = configuration_field("WORKER_DATABASE_SESSION_CHECK_SECONDS", lambda: _as_float("WORKER_DATABASE_SESSION_CHECK_SECONDS", 60))
    worker_database_available_value: str = configuration_field("WORKER_DATABASE_AVAILABLE_VALUE", lambda: _environment.get().get(
        "WORKER_DATABASE_AVAILABLE_VALUE", "1"
    ).strip())
    # 切割文件索引/内容库单独配置，不与生产报表或 HR 库共用账号。
    file_database_enabled: bool = configuration_field("FILE_DATABASE_ENABLED", lambda: _as_bool("FILE_DATABASE_ENABLED", False))
    file_sqlserver_host: str = configuration_field("FILE_SQLSERVER_HOST", lambda: _environment.get().get("FILE_SQLSERVER_HOST", ""))
    file_sqlserver_port: int = configuration_field("FILE_SQLSERVER_PORT", lambda: _as_int("FILE_SQLSERVER_PORT", 1433))
    file_sqlserver_database: str = configuration_field("FILE_SQLSERVER_DATABASE", lambda: _environment.get().get("FILE_SQLSERVER_DATABASE", ""))
    file_sqlserver_username: str = configuration_field("FILE_SQLSERVER_USERNAME", lambda: _environment.get().get("FILE_SQLSERVER_USERNAME", ""))
    file_sqlserver_password: str = configuration_field("FILE_SQLSERVER_PASSWORD", lambda: _environment.get().get("FILE_SQLSERVER_PASSWORD", ""))
    file_sqlserver_driver: str = configuration_field("FILE_SQLSERVER_DRIVER", lambda: _environment.get().get("FILE_SQLSERVER_DRIVER", ""))
    file_sqlserver_encrypt: str = configuration_field("FILE_SQLSERVER_ENCRYPT", lambda: _environment.get().get("FILE_SQLSERVER_ENCRYPT", ""))
    file_sqlserver_trust_server_certificate: str = configuration_field("FILE_SQLSERVER_TRUST_SERVER_CERTIFICATE", lambda: _environment.get().get(
        "FILE_SQLSERVER_TRUST_SERVER_CERTIFICATE", ""
    ))
    file_sqlserver_connect_timeout: int = configuration_field("FILE_SQLSERVER_CONNECT_TIMEOUT_SECONDS", lambda: _as_int(
        "FILE_SQLSERVER_CONNECT_TIMEOUT_SECONDS", 5
    ))
    file_sqlserver_query_timeout: int = configuration_field("FILE_SQLSERVER_QUERY_TIMEOUT_SECONDS", lambda: _as_int(
        "FILE_SQLSERVER_QUERY_TIMEOUT_SECONDS", 15
    ))
    file_status_sent_value: str = configuration_field("FILE_STATUS_SENT_VALUE", lambda: _environment.get().get(
        "FILE_STATUS_SENT_VALUE", "已派发"
    ).strip())
    file_status_failed_value: str = configuration_field("FILE_STATUS_FAILED_VALUE", lambda: _environment.get().get(
        "FILE_STATUS_FAILED_VALUE", "派发失败"
    ).strip())
    file_source_mode: str = configuration_field("FILE_SOURCE_MODE", lambda: _environment.get().get("FILE_SOURCE_MODE", "disabled").strip().lower())
    file_source_local_root: str = configuration_field("FILE_SOURCE_LOCAL_ROOT", lambda: _environment.get().get("FILE_SOURCE_LOCAL_ROOT", "").strip())
    file_http_base_url: str = configuration_field("FILE_HTTP_BASE_URL", lambda: _environment.get().get("FILE_HTTP_BASE_URL", "http://10.147.0.17:8800").strip())
    file_http_authorization: str = configuration_field("FILE_HTTP_AUTHORIZATION", lambda: _environment.get().get("FILE_HTTP_AUTHORIZATION", ""), hidden=True)
    file_http_cookie: str = configuration_field("FILE_HTTP_COOKIE", lambda: _environment.get().get("FILE_HTTP_COOKIE", ""), hidden=True)
    file_download_timeout: float = configuration_field("FILE_DOWNLOAD_TIMEOUT_SECONDS", lambda: _as_float("FILE_DOWNLOAD_TIMEOUT_SECONDS", 60))
    file_download_max_bytes: int = configuration_field("FILE_DOWNLOAD_MAX_BYTES", lambda: _as_int("FILE_DOWNLOAD_MAX_BYTES", 52428800))
    # Successful downloads are kept on disk for this many days. Set to 0 to
    # disable automatic physical-file cleanup while retaining all audit rows.
    file_download_retention_days: int = configuration_field("FILE_DOWNLOAD_RETENTION_DAYS", lambda: _as_int("FILE_DOWNLOAD_RETENTION_DAYS", 7))
    file_server_host: str = configuration_field("FILE_SERVER_HOST", lambda: _environment.get().get("FILE_SERVER_HOST", "10.147.0.33").strip())
    file_server_physical_root: str = configuration_field("FILE_SERVER_PHYSICAL_ROOT", lambda: _environment.get().get(
        "FILE_SERVER_PHYSICAL_ROOT", r"D:\File"
    ).strip())
    magic_box_enabled: bool = configuration_field("MAGIC_BOX_ENABLED", lambda: _as_bool("MAGIC_BOX_ENABLED", False))
    magic_box_timeout: float = configuration_field("MAGIC_BOX_TIMEOUT_SECONDS", lambda: _as_float("MAGIC_BOX_TIMEOUT_SECONDS", 60))
    magic_box_operation_timeout: float = configuration_field("MAGIC_BOX_OPERATION_TIMEOUT_SECONDS", lambda: _as_float("MAGIC_BOX_OPERATION_TIMEOUT_SECONDS", 180))
    magic_box_config_cache_seconds: int = configuration_field("MAGIC_BOX_CONFIG_CACHE_SECONDS", lambda: _as_int(
        "MAGIC_BOX_CONFIG_CACHE_SECONDS", 60
    ))

    llm_enabled: bool = configuration_field("LLM_ENABLED", lambda: _as_bool("LLM_ENABLED", False))
    llm_api_key: str = configuration_field("LLM_API_KEY", lambda: _environment.get().get("LLM_API_KEY", ""))
    llm_base_url: str = configuration_field("LLM_BASE_URL", lambda: _environment.get().get("LLM_BASE_URL", ""))
    llm_model: str = configuration_field("LLM_MODEL", lambda: _environment.get().get("LLM_MODEL", ""))
    llm_timeout: float = configuration_field("LLM_TIMEOUT_SECONDS", lambda: _as_float("LLM_TIMEOUT_SECONDS", 180))
    llm_max_input_chars: int = configuration_field("LLM_MAX_INPUT_CHARS", lambda: _as_int("LLM_MAX_INPUT_CHARS", 500_000))
    llm_max_output_chars: int = configuration_field("LLM_MAX_OUTPUT_CHARS", lambda: _as_int("LLM_MAX_OUTPUT_CHARS", 50_000))
    llm_max_concurrent: int = configuration_field("LLM_MAX_CONCURRENT", lambda: _as_int("LLM_MAX_CONCURRENT", 10))
    llm_queue_timeout_seconds: float = configuration_field("LLM_QUEUE_TIMEOUT_SECONDS", lambda: _as_float("LLM_QUEUE_TIMEOUT_SECONDS", 120))

    # 告警源沿用生产数据库，设备范围由目录提供。
    alarm_monitor_enabled: bool = configuration_field("ALARM_MONITOR_ENABLED", lambda: _as_bool("ALARM_MONITOR_ENABLED", False))
    alarm_monitor_interval_seconds: int = configuration_field("ALARM_MONITOR_INTERVAL_SECONDS", lambda: _as_int(
        "ALARM_MONITOR_INTERVAL_SECONDS", 300
    ))
    alarm_monitor_machine_nos: tuple[str, ...] = (DEFAULT_DEVICE.machine_no,)
    alarm_monitor_start_at: str = DEFAULT_DEVICE.alarm.start_at
    alarm_reopen_debounce_minutes: int = configuration_field("ALARM_REOPEN_DEBOUNCE_MINUTES", lambda: _as_int(
        "ALARM_REOPEN_DEBOUNCE_MINUTES", 10
    ))
    alarm_monitor_batch_size: int = configuration_field("ALARM_MONITOR_BATCH_SIZE", lambda: _as_int("ALARM_MONITOR_BATCH_SIZE", 500))

    # Unified operational-data retention. A value of 0 disables cleanup for
    # that category without disabling the remaining policies.
    report_retention_days: int = configuration_field("REPORT_RETENTION_DAYS", lambda: _as_int("REPORT_RETENTION_DAYS", 365))
    analysis_retention_days: int = configuration_field("ANALYSIS_RETENTION_DAYS", lambda: _as_int("ANALYSIS_RETENTION_DAYS", 90))
    alarm_retention_days: int = configuration_field("ALARM_RETENTION_DAYS", lambda: _as_int("ALARM_RETENTION_DAYS", 30))
    dispatch_retention_days: int = configuration_field("DISPATCH_RETENTION_DAYS", lambda: _as_int("DISPATCH_RETENTION_DAYS", 365))
    audit_retention_days: int = configuration_field("AUDIT_RETENTION_DAYS", lambda: _as_int("AUDIT_RETENTION_DAYS", 730))
    retention_cleanup_interval_hours: int = configuration_field("RETENTION_CLEANUP_INTERVAL_HOURS", lambda: _as_int(
        "RETENTION_CLEANUP_INTERVAL_HOURS", 24
    ))
    retention_cleanup_batch_size: int = configuration_field("RETENTION_CLEANUP_BATCH_SIZE", lambda: _as_int(
        "RETENTION_CLEANUP_BATCH_SIZE", 500
    ))

    report_max_detail_rows: int = configuration_field("REPORT_MAX_DETAIL_ROWS", lambda: _as_int("REPORT_MAX_DETAIL_ROWS", 1000))
    chart_top_n: int = configuration_field("CHART_TOP_N", lambda: _as_int("CHART_TOP_N", 20))
    worker_poll_seconds: float = configuration_field("REPORT_WORKER_POLL_SECONDS", lambda: _as_float("REPORT_WORKER_POLL_SECONDS", 0.5))
    worker_stall_timeout_seconds: float = configuration_field("REPORT_WORKER_STALL_TIMEOUT_SECONDS", lambda: _as_float("REPORT_WORKER_STALL_TIMEOUT_SECONDS", 900))
    report_operation_timeout_seconds: float = configuration_field("REPORT_OPERATION_TIMEOUT_SECONDS", lambda: _as_float("REPORT_OPERATION_TIMEOUT_SECONDS", 900))
    pdf_render_timeout_seconds: float = configuration_field("PDF_RENDER_TIMEOUT_SECONDS", lambda: _as_float("PDF_RENDER_TIMEOUT_SECONDS", 120))
    automation_enabled: bool = configuration_field("AUTOMATION_ENABLED", lambda: _as_bool("AUTOMATION_ENABLED", True))
    automation_scan_seconds: float = configuration_field("AUTOMATION_SCAN_SECONDS", lambda: _as_float("AUTOMATION_SCAN_SECONDS", 15))
    email_retry_base_seconds: int = configuration_field("EMAIL_RETRY_BASE_SECONDS", lambda: _as_int("EMAIL_RETRY_BASE_SECONDS", 60))
    smtp_enabled: bool = configuration_field("SMTP_ENABLED", lambda: _as_bool("SMTP_ENABLED", False))
    smtp_host: str = configuration_field("SMTP_HOST", lambda: _environment.get().get("SMTP_HOST", ""))
    smtp_port: int = configuration_field("SMTP_PORT", lambda: _as_int("SMTP_PORT", 587))
    smtp_username: str = configuration_field("SMTP_USERNAME", lambda: _environment.get().get("SMTP_USERNAME", ""))
    smtp_password: str = configuration_field("SMTP_PASSWORD", lambda: _environment.get().get("SMTP_PASSWORD", ""))
    smtp_from_email: str = configuration_field("SMTP_FROM_EMAIL", lambda: _environment.get().get("SMTP_FROM_EMAIL", ""))
    smtp_from_name: str = configuration_field("SMTP_FROM_NAME", lambda: _environment.get().get("SMTP_FROM_NAME", "切割生产智能产报系统"))
    smtp_use_tls: bool = configuration_field("SMTP_USE_TLS", lambda: _as_bool("SMTP_USE_TLS", True))
    smtp_use_ssl: bool = configuration_field("SMTP_USE_SSL", lambda: _as_bool("SMTP_USE_SSL", False))
    smtp_timeout: float = configuration_field("SMTP_TIMEOUT_SECONDS", lambda: _as_float("SMTP_TIMEOUT_SECONDS", 20))
    sql_workbench_enabled: bool = configuration_field("SQL_WORKBENCH_ENABLED", lambda: _as_bool("SQL_WORKBENCH_ENABLED", False))
    sql_workbench_admin_token: str = configuration_field("SQL_WORKBENCH_ADMIN_TOKEN", lambda: _environment.get().get("SQL_WORKBENCH_ADMIN_TOKEN", ""))
    sql_workbench_max_rows: int = configuration_field("SQL_WORKBENCH_MAX_ROWS", lambda: _as_int("SQL_WORKBENCH_MAX_ROWS", 200))
    auth_session_hours: int = configuration_field("AUTH_SESSION_HOURS", lambda: _as_int("AUTH_SESSION_HOURS", 12))
    auth_cookie_secure: bool = configuration_field("AUTH_COOKIE_SECURE", lambda: _as_bool("AUTH_COOKIE_SECURE", False))

    def __post_init__(self) -> None:
        if not 1000 <= self.llm_max_input_chars <= 10_000_000:
            raise ValueError("LLM_MAX_INPUT_CHARS 必须在 1000 到 10000000 之间")
        if not 1000 <= self.llm_max_output_chars <= 2_000_000:
            raise ValueError("LLM_MAX_OUTPUT_CHARS 必须在 1000 到 2000000 之间")
        if not 1 <= self.llm_max_concurrent <= 10:
            raise ValueError("LLM_MAX_CONCURRENT 必须在 1 到 10 之间")
        if not 1 <= self.llm_queue_timeout_seconds <= 300:
            raise ValueError("LLM_QUEUE_TIMEOUT_SECONDS 必须在 1 到 300 秒之间")
        if not 1 <= self.llm_timeout <= 300:
            raise ValueError("LLM_TIMEOUT_SECONDS 必须在 1 到 300 秒之间")
        if not 1 <= self.worker_database_session_check_seconds <= 3600:
            raise ValueError("WORKER_DATABASE_SESSION_CHECK_SECONDS 必须在 1 到 3600 秒之间")
        if not 1 <= self.worker_stall_timeout_seconds <= 86400:
            raise ValueError("REPORT_WORKER_STALL_TIMEOUT_SECONDS 必须在 1 到 86400 秒之间")
        if not 5 <= self.report_operation_timeout_seconds <= 86400:
            raise ValueError("REPORT_OPERATION_TIMEOUT_SECONDS 必须在 5 到 86400 秒之间")
        if not 1 <= self.pdf_render_timeout_seconds <= 1800:
            raise ValueError("PDF_RENDER_TIMEOUT_SECONDS 必须在 1 到 1800 秒之间")
        if not 5 <= self.magic_box_operation_timeout <= 1800:
            raise ValueError("MAGIC_BOX_OPERATION_TIMEOUT_SECONDS 必须在 5 到 1800 秒之间")
        if self.data_source_mode not in {"", "demo", "sqlserver"}:
            raise ValueError("APP_DATA_SOURCE_MODE 必须是 demo 或 sqlserver；Excel 数据源已移除，请使用 SQL Server")
        if (
            self.app_env.strip().lower() in {"production", "prod"}
            and self.demo_mode and not self.data_source_mode
        ):
            raise ValueError("生产环境禁止隐式演示数据；使用演示数据必须显式设置 APP_DATA_SOURCE_MODE=demo")

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def metadata_db_path(self) -> Path:
        return self.data_dir / "metadata.db"

    @property
    def cutting_downloads_dir(self) -> Path:
        return self.data_dir / "cutting-downloads"

    @property
    def runtime_settings_env_path(self) -> Path:
        return self.data_dir / "settings.env"

    @property
    def reports_dir(self) -> Path:
        return self.project_root / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def frontend_dist_dir(self) -> Path:
        return self.project_root / "frontend" / "dist"

    @property
    def echarts_vendor_path(self) -> Path:
        return self.project_root / "vendor" / "echarts.min.js"

    @property
    def effective_data_source_mode(self) -> str:
        mode = self.data_source_mode or ("demo" if self.demo_mode else "sqlserver")
        return mode

    def sqlserver_connection(self, source: str = "production") -> dict[str, object]:
        """Return one complete connection profile without mixing database identities."""
        if source not in {"production", "worker", "file"}:
            raise ValueError(f"不支持的数据源：{source}")

        primary = {
            "host": self.sqlserver_host,
            "port": self.sqlserver_port,
            "database": self.sqlserver_database,
            "username": self.sqlserver_username,
            "password": self.sqlserver_password,
            "driver": self.sqlserver_driver,
            "encrypt": self.sqlserver_encrypt,
            "trust_server_certificate": self.sqlserver_trust_server_certificate,
            "connect_timeout": self.sqlserver_connect_timeout,
            "query_timeout": self.sqlserver_query_timeout,
            "uses_primary_fallback": False,
        }
        if source == "production":
            return primary

        dedicated_identity = {
            "host": getattr(self, f"{source}_sqlserver_host"),
            "database": getattr(self, f"{source}_sqlserver_database"),
            "username": getattr(self, f"{source}_sqlserver_username"),
            "password": getattr(self, f"{source}_sqlserver_password"),
        }
        if not any(dedicated_identity.values()):
            return {**primary, "uses_primary_fallback": True}

        return {
            **dedicated_identity,
            "port": getattr(self, f"{source}_sqlserver_port"),
            "driver": getattr(self, f"{source}_sqlserver_driver") or self.sqlserver_driver,
            "encrypt": getattr(self, f"{source}_sqlserver_encrypt") or self.sqlserver_encrypt,
            "trust_server_certificate": (
                getattr(self, f"{source}_sqlserver_trust_server_certificate")
                or self.sqlserver_trust_server_certificate
            ),
            "connect_timeout": getattr(
                self, f"{source}_sqlserver_connect_timeout"
            ),
            "query_timeout": getattr(self, f"{source}_sqlserver_query_timeout"),
            "uses_primary_fallback": False,
        }

    def ensure_runtime_dirs(self) -> None:
        for path in (self.data_dir, self.reports_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


def settings_for_device(base: Settings, device_id: str) -> Settings:
    device = next((item for item in DEVICE_CATALOG.devices if item.id == device_id), None)
    if device is None:
        raise ValueError("设备不存在")
    energy = device.energy
    return replace(
        base, device_id=device.id, device_name=device.name,
        production_line_id=device.production_line_id, production_data_source=device.data_source,
        report_machine_no=device.machine_no, energy_connected=energy is not None,
        energy_api_enabled=base.energy_api_enabled and energy is not None,
        energy_gas_parameter_id=str(energy.gas_parameter_id) if energy else "",
        energy_report_equipment_ids=energy.equipment_ids if energy else (device.machine_no,),
        energy_report_electricity_parameter_ids=tuple(map(str, energy.electricity_parameter_ids)) if energy else (),
        energy_equipment_ids=(), energy_electricity_parameter_ids=(),
        alarm_monitor_machine_nos=(device.machine_no,),
        alarm_monitor_start_at=device.alarm.start_at,
    )


settings = Settings()
