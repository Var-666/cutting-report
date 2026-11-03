from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path

from app.configuration_sources import load_environment

from app.device_config import ProductionLineInstance


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
    device_id: str = ""
    device_name: str = ""
    production_line_id: str = ""
    production_data_source: str = ""
    energy_connected: bool = False
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

    production_lines_json: str = configuration_field("PRODUCTION_LINES_JSON", lambda: _environment.get().get("PRODUCTION_LINES_JSON", ""))
    worker_source_key: str = configuration_field("WORKER_SOURCE_KEY", lambda: _environment.get().get("WORKER_SOURCE_KEY", ""))
    file_source_key: str = configuration_field("FILE_SOURCE_KEY", lambda: _environment.get().get("FILE_SOURCE_KEY", ""))
    dispatch_source_key: str = configuration_field("DISPATCH_SOURCE_KEY", lambda: _environment.get().get("DISPATCH_SOURCE_KEY", ""))
    source_fingerprint: str = ""
    alarm_source_name: str = ""

    database_sources_json: str = configuration_field("DATABASE_SOURCES_JSON", lambda: _environment.get().get("DATABASE_SOURCES_JSON", "[]"), hidden=True)

    sql_global_max_concurrent: int = configuration_field('SQL_GLOBAL_MAX_CONCURRENT', lambda: _as_int('SQL_GLOBAL_MAX_CONCURRENT', 12))
    sql_global_max_waiters: int = configuration_field('SQL_GLOBAL_MAX_WAITERS', lambda: _as_int('SQL_GLOBAL_MAX_WAITERS', 32))
    sql_queue_timeout_seconds: float = configuration_field('SQL_QUEUE_TIMEOUT_SECONDS', lambda: _as_float('SQL_QUEUE_TIMEOUT_SECONDS', 5))
    sqlserver_default_max_concurrent: int = configuration_field('SQLSERVER_DEFAULT_MAX_CONCURRENT', lambda: _as_int('SQLSERVER_DEFAULT_MAX_CONCURRENT', 6))
    mysql_default_max_concurrent: int = configuration_field('MYSQL_DEFAULT_MAX_CONCURRENT', lambda: _as_int('MYSQL_DEFAULT_MAX_CONCURRENT', 4))
    sql_line_max_concurrent: int = configuration_field('SQL_LINE_MAX_CONCURRENT', lambda: _as_int('SQL_LINE_MAX_CONCURRENT', 2))
    http_query_max_concurrent: int = configuration_field('HTTP_QUERY_MAX_CONCURRENT', lambda: _as_int('HTTP_QUERY_MAX_CONCURRENT', 12))
    http_snapshot_max_concurrent: int = configuration_field('HTTP_SNAPSHOT_MAX_CONCURRENT', lambda: _as_int('HTTP_SNAPSHOT_MAX_CONCURRENT', 4))
    http_analysis_max_concurrent: int = configuration_field('HTTP_ANALYSIS_MAX_CONCURRENT', lambda: _as_int('HTTP_ANALYSIS_MAX_CONCURRENT', 10))
    http_pdf_max_concurrent: int = configuration_field('HTTP_PDF_MAX_CONCURRENT', lambda: _as_int('HTTP_PDF_MAX_CONCURRENT', 2))
    http_auth_max_concurrent: int = configuration_field('HTTP_AUTH_MAX_CONCURRENT', lambda: _as_int('HTTP_AUTH_MAX_CONCURRENT', 8))
    http_max_waiters: int = configuration_field('HTTP_MAX_WAITERS', lambda: _as_int('HTTP_MAX_WAITERS', 32))

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
    report_machine_no: str = ""

    weld_enabled: bool = False
    weld_db_timezone: str = "Asia/Shanghai"

    # 能管平台只读接入。仪表绑定、计量边界和单位必须由现场确认。
    energy_api_enabled: bool = configuration_field("ENERGY_API_ENABLED", lambda: _as_bool("ENERGY_API_ENABLED", False))
    energy_api_base_url: str = configuration_field("ENERGY_API_BASE_URL", lambda: _environment.get().get("ENERGY_API_BASE_URL", "http://10.147.0.38:10091"))
    energy_api_username: str = configuration_field("ENERGY_API_USERNAME", lambda: _environment.get().get("ENERGY_API_USERNAME", "").strip())
    energy_api_password: str = configuration_field("ENERGY_API_PASSWORD", lambda: _environment.get().get("ENERGY_API_PASSWORD", ""), hidden=True)
    energy_api_timeout: float = configuration_field("ENERGY_API_TIMEOUT_SECONDS", lambda: _as_float("ENERGY_API_TIMEOUT_SECONDS", 10))
    energy_cache_seconds: int = configuration_field("ENERGY_CACHE_SECONDS", lambda: _as_int("ENERGY_CACHE_SECONDS", 60))
    energy_equipment_ids: tuple[str, ...] = ()
    energy_gas_parameter_id: str = ""
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
    energy_report_equipment_ids: tuple[str, ...] = ()
    energy_report_electricity_parameter_ids: tuple[str, ...] = ()
    energy_report_max_gap_seconds: int = configuration_field("ENERGY_REPORT_MAX_GAP_SECONDS", lambda: _as_int("ENERGY_REPORT_MAX_GAP_SECONDS", 1800))
    energy_report_snapshot_ttl_seconds: int = configuration_field("ENERGY_REPORT_SNAPSHOT_TTL_SECONDS", lambda: _as_int("ENERGY_REPORT_SNAPSHOT_TTL_SECONDS", 3600))

    worker_database_auth_enabled: bool = configuration_field("WORKER_DATABASE_AUTH_ENABLED", lambda: _as_bool(
        "WORKER_DATABASE_AUTH_ENABLED", False
    ))
    worker_database_session_check_seconds: float = configuration_field("WORKER_DATABASE_SESSION_CHECK_SECONDS", lambda: _as_float("WORKER_DATABASE_SESSION_CHECK_SECONDS", 60))
    worker_database_available_value: str = configuration_field("WORKER_DATABASE_AVAILABLE_VALUE", lambda: _environment.get().get(
        "WORKER_DATABASE_AVAILABLE_VALUE", "1"
    ).strip())
    # 切割文件索引/内容库单独配置，不与生产报表或 HR 库共用账号。
    file_database_enabled: bool = configuration_field("FILE_DATABASE_ENABLED", lambda: _as_bool("FILE_DATABASE_ENABLED", False))
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
    alarm_monitor_machine_nos: tuple[str, ...] = ()
    alarm_monitor_start_at: str = ""
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
        for name in ('sql_global_max_concurrent', 'sqlserver_default_max_concurrent', 'mysql_default_max_concurrent',
                     'sql_line_max_concurrent', 'http_query_max_concurrent', 'http_snapshot_max_concurrent',
                     'http_analysis_max_concurrent', 'http_pdf_max_concurrent', 'http_auth_max_concurrent'):
            if not 1 <= getattr(self, name) <= 64:
                raise ValueError(f'{name.upper()} 必须在 1 到 64 之间')
        if not 0 <= self.sql_global_max_waiters <= 256 or not 0 <= self.http_max_waiters <= 256:
            raise ValueError('SQL_GLOBAL_MAX_WAITERS / HTTP_MAX_WAITERS 必须在 0 到 256 之间')
        if not 0 < self.sql_queue_timeout_seconds <= 60:
            raise ValueError('SQL_QUEUE_TIMEOUT_SECONDS 必须在 0 到 60 秒之间')
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

    def ensure_runtime_dirs(self) -> None:
        for path in (self.data_dir, self.reports_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


def settings_for_line(base: Settings, line: ProductionLineInstance, source_fingerprint: str = "") -> Settings:
    energy = line.energy
    return replace(
        base, device_id=line.id, device_name=line.name,
        production_line_id=line.production_line_id or line.id, production_data_source=line.source_key,
        source_fingerprint=source_fingerprint,
        report_machine_no=line.machine_no or "", energy_connected=energy is not None,
        energy_api_enabled=base.energy_api_enabled and energy is not None,
        energy_gas_parameter_id=str(energy.gas_parameter_id) if energy else "",
        energy_report_equipment_ids=energy.equipment_ids if energy else ((line.machine_no,) if line.machine_no else ()),
        energy_report_electricity_parameter_ids=tuple(map(str, energy.electricity_parameter_ids)) if energy else (),
        energy_equipment_ids=(), energy_electricity_parameter_ids=(),
        alarm_monitor_machine_nos=(line.machine_no,) if line.machine_no else (),
        alarm_monitor_start_at=line.alarm.start_at if line.alarm else "",
        weld_enabled=line.enabled, weld_db_timezone=line.timezone,
    )


settings = Settings()
