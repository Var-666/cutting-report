from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SETTINGS_ENV_PATH = PROJECT_ROOT / "data" / "settings.env"
load_dotenv(PROJECT_ROOT / ".env")
# 管理界面写入的受控配置放在持久化 data 目录。后加载并覆盖
# 项目 .env，使 Docker 命名卷和 Windows 本地部署都能在重启后生效。
load_dotenv(RUNTIME_SETTINGS_ENV_PATH, override=True, interpolate=False)


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _as_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = _as_int("APP_PORT", 8000)
    timezone: str = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
    app_version: str = os.getenv("APP_VERSION", "0.1.0")
    demo_mode: bool = _as_bool("APP_DEMO_MODE", True)
    data_source_mode: str = os.getenv("APP_DATA_SOURCE_MODE", "").strip().lower()
    task_excel_file: str = os.getenv("TASK_EXCEL_PATH", "TASK.xlsx")
    cors_origins: tuple[str, ...] = tuple(
        item.strip()
        for item in os.getenv("APP_CORS_ORIGINS", "http://localhost:5173").split(",")
        if item.strip()
    )

    sqlserver_host: str = os.getenv("SQLSERVER_HOST", "")
    sqlserver_port: int = _as_int("SQLSERVER_PORT", 1433)
    sqlserver_database: str = os.getenv("SQLSERVER_DATABASE", "")
    sqlserver_username: str = os.getenv("SQLSERVER_USERNAME", "")
    sqlserver_password: str = os.getenv("SQLSERVER_PASSWORD", "")
    sqlserver_driver: str = os.getenv("SQLSERVER_DRIVER", "ODBC Driver 18 for SQL Server")
    sqlserver_encrypt: str = os.getenv("SQLSERVER_ENCRYPT", "yes")
    sqlserver_trust_server_certificate: str = os.getenv(
        "SQLSERVER_TRUST_SERVER_CERTIFICATE", "yes"
    )
    sqlserver_connect_timeout: int = _as_int("SQLSERVER_CONNECT_TIMEOUT_SECONDS", 5)
    sqlserver_query_timeout: int = _as_int("SQLSERVER_QUERY_TIMEOUT_SECONDS", 120)
    leadership_report_cache_seconds: int = _as_int(
        "LEADERSHIP_REPORT_CACHE_SECONDS", 300
    )
    leadership_report_history_cache_seconds: int = _as_int(
        "LEADERSHIP_REPORT_HISTORY_CACHE_SECONDS", 86400
    )
    leadership_report_reconcile_hour: int = _as_int(
        "LEADERSHIP_REPORT_RECONCILE_HOUR", 2
    )
    report_data_cache_seconds: int = _as_int("REPORT_DATA_CACHE_SECONDS", 300)
    # 当前所有生产统计统一限定为 dbo.task.MACHINE_NO = Cutter01。
    report_machine_no: str = os.getenv("REPORT_MACHINE_NO", "Cutter01").strip() or "Cutter01"

    # 能管平台只读接入。仪表绑定、计量边界和单位必须由现场确认。
    energy_api_enabled: bool = _as_bool("ENERGY_API_ENABLED", False)
    energy_api_base_url: str = os.getenv("ENERGY_API_BASE_URL", "http://10.147.0.38:10091")
    energy_api_username: str = os.getenv("ENERGY_API_USERNAME", "").strip()
    energy_api_password: str = field(default=os.getenv("ENERGY_API_PASSWORD", ""), repr=False)
    energy_api_timeout: float = _as_float("ENERGY_API_TIMEOUT_SECONDS", 10)
    energy_cache_seconds: int = _as_int("ENERGY_CACHE_SECONDS", 60)
    energy_equipment_ids: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv("ENERGY_EQUIPMENT_IDS", "").split(",") if value.strip()
    )
    energy_gas_parameter_id: str = os.getenv(
        "ENERGY_GAS_PARAMETER_ID", "bd3179be-c587-f011-a2e9-000c2974f33c"
    ).strip()
    energy_electricity_parameter_ids: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv("ENERGY_ELECTRICITY_PARAMETER_IDS", "").split(",") if value.strip()
    )
    energy_gas_unit: str = os.getenv("ENERGY_GAS_UNIT", "Nm³").strip()
    energy_electricity_unit: str = os.getenv("ENERGY_ELECTRICITY_UNIT", "kWh").strip()
    # 平台 Mv 已乘读数因子；以下仅用于经确认的单位转换，禁止再次乘 CT/PT。
    energy_gas_factor: str = os.getenv("ENERGY_GAS_FACTOR", "1")
    energy_electricity_factor: str = os.getenv("ENERGY_ELECTRICITY_FACTOR", "1")
    energy_max_gap_seconds: int = _as_int("ENERGY_MAX_GAP_SECONDS", 300)
    energy_allow_interpolation: bool = _as_bool("ENERGY_ALLOW_INTERPOLATION", True)
    # 留空不启用新规则；时间按 APP_TIMEZONE 解释，也可显式附带时区。
    energy_sampling_change_at: str = os.getenv(
        "ENERGY_SAMPLING_CHANGE_AT", "2026-09-04T00:00:00+08:00"
    ).strip()
    energy_new_max_gap_seconds: int = _as_int("ENERGY_NEW_MAX_GAP_SECONDS", 180)

    # 统一设备能耗报表：当前仪表组只归属一台物理设备。多个值仅用于
    # 同一设备在 TASK.MACHINE_NO 中的历史别名，不能混入另一台设备。
    energy_report_equipment_ids: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv(
            "ENERGY_REPORT_EQUIPMENT_IDS", "Cutter01"
        ).split(",") if value.strip()
    )
    energy_report_electricity_parameter_ids: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv(
            "ENERGY_REPORT_ELECTRICITY_PARAMETER_IDS",
            "2b00e015-d987-f011-a2e9-000c2974f33c,98cbd135-d987-f011-a2e9-000c2974f33c,93fae152-d987-f011-a2e9-000c2974f33c",
        ).split(",") if value.strip()
    )
    energy_report_max_gap_seconds: int = _as_int("ENERGY_REPORT_MAX_GAP_SECONDS", 1800)
    energy_report_snapshot_ttl_seconds: int = _as_int("ENERGY_REPORT_SNAPSHOT_TTL_SECONDS", 3600)

    # HR 工人库与生产任务库可以位于不同的 SQL Server 实例。
    # 专用地址、库名、账号或密码任意一项被配置后，该数据源就不再
    # 混用生产库的连接字段；旧部署在完全未配置时仍可显式回退。
    worker_sqlserver_host: str = os.getenv("WORKER_SQLSERVER_HOST", "")
    worker_sqlserver_port: int = _as_int("WORKER_SQLSERVER_PORT", 1433)
    worker_sqlserver_database: str = os.getenv("WORKER_SQLSERVER_DATABASE", "")
    worker_sqlserver_username: str = os.getenv("WORKER_SQLSERVER_USERNAME", "")
    worker_sqlserver_password: str = os.getenv("WORKER_SQLSERVER_PASSWORD", "")
    worker_sqlserver_driver: str = os.getenv("WORKER_SQLSERVER_DRIVER", "")
    worker_sqlserver_encrypt: str = os.getenv("WORKER_SQLSERVER_ENCRYPT", "")
    worker_sqlserver_trust_server_certificate: str = os.getenv(
        "WORKER_SQLSERVER_TRUST_SERVER_CERTIFICATE", ""
    )
    worker_sqlserver_connect_timeout: int = _as_int(
        "WORKER_SQLSERVER_CONNECT_TIMEOUT_SECONDS", 5
    )
    worker_sqlserver_query_timeout: int = _as_int(
        "WORKER_SQLSERVER_QUERY_TIMEOUT_SECONDS", 15
    )

    worker_database_auth_enabled: bool = _as_bool(
        "WORKER_DATABASE_AUTH_ENABLED", False
    )
    worker_database_available_value: str = os.getenv(
        "WORKER_DATABASE_AVAILABLE_VALUE", "1"
    ).strip()
    # 切割文件索引/内容库单独配置，不与生产报表或 HR 库共用账号。
    file_database_enabled: bool = _as_bool("FILE_DATABASE_ENABLED", False)
    file_sqlserver_host: str = os.getenv("FILE_SQLSERVER_HOST", "")
    file_sqlserver_port: int = _as_int("FILE_SQLSERVER_PORT", 1433)
    file_sqlserver_database: str = os.getenv("FILE_SQLSERVER_DATABASE", "")
    file_sqlserver_username: str = os.getenv("FILE_SQLSERVER_USERNAME", "")
    file_sqlserver_password: str = os.getenv("FILE_SQLSERVER_PASSWORD", "")
    file_sqlserver_driver: str = os.getenv("FILE_SQLSERVER_DRIVER", "")
    file_sqlserver_encrypt: str = os.getenv("FILE_SQLSERVER_ENCRYPT", "")
    file_sqlserver_trust_server_certificate: str = os.getenv(
        "FILE_SQLSERVER_TRUST_SERVER_CERTIFICATE", ""
    )
    file_sqlserver_connect_timeout: int = _as_int(
        "FILE_SQLSERVER_CONNECT_TIMEOUT_SECONDS", 5
    )
    file_sqlserver_query_timeout: int = _as_int(
        "FILE_SQLSERVER_QUERY_TIMEOUT_SECONDS", 15
    )
    file_status_sent_value: str = os.getenv(
        "FILE_STATUS_SENT_VALUE", "已派发"
    ).strip()
    file_status_failed_value: str = os.getenv(
        "FILE_STATUS_FAILED_VALUE", "派发失败"
    ).strip()
    file_source_mode: str = os.getenv("FILE_SOURCE_MODE", "disabled").strip().lower()
    file_source_local_root: str = os.getenv("FILE_SOURCE_LOCAL_ROOT", "").strip()
    file_http_base_url: str = os.getenv("FILE_HTTP_BASE_URL", "http://10.147.0.17:8800").strip()
    file_http_authorization: str = field(default=os.getenv("FILE_HTTP_AUTHORIZATION", ""), repr=False)
    file_http_cookie: str = field(default=os.getenv("FILE_HTTP_COOKIE", ""), repr=False)
    file_download_timeout: float = _as_float("FILE_DOWNLOAD_TIMEOUT_SECONDS", 60)
    file_download_max_bytes: int = _as_int("FILE_DOWNLOAD_MAX_BYTES", 52428800)
    file_server_host: str = os.getenv("FILE_SERVER_HOST", "10.147.0.33").strip()
    file_server_physical_root: str = os.getenv(
        "FILE_SERVER_PHYSICAL_ROOT", r"D:\File"
    ).strip()
    magic_box_enabled: bool = _as_bool("MAGIC_BOX_ENABLED", False)
    magic_box_timeout: float = _as_float("MAGIC_BOX_TIMEOUT_SECONDS", 60)
    magic_box_config_cache_seconds: int = _as_int(
        "MAGIC_BOX_CONFIG_CACHE_SECONDS", 60
    )

    llm_enabled: bool = _as_bool("LLM_ENABLED", False)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout: float = _as_float("LLM_TIMEOUT_SECONDS", 30)

    # 告警源位于生产数据库；首期只监控数据库中的实际设备名“20kw切割机”。
    alarm_monitor_enabled: bool = _as_bool("ALARM_MONITOR_ENABLED", False)
    alarm_monitor_interval_seconds: int = _as_int(
        "ALARM_MONITOR_INTERVAL_SECONDS", 300
    )
    alarm_monitor_machine_nos: tuple[str, ...] = tuple(
        value.strip()
        for value in os.getenv("ALARM_MONITOR_MACHINE_NOS", "20kw切割机").split(",")
        if value.strip()
    )
    alarm_reopen_debounce_minutes: int = _as_int(
        "ALARM_REOPEN_DEBOUNCE_MINUTES", 10
    )
    alarm_monitor_batch_size: int = _as_int("ALARM_MONITOR_BATCH_SIZE", 500)

    report_max_detail_rows: int = _as_int("REPORT_MAX_DETAIL_ROWS", 1000)
    chart_top_n: int = _as_int("CHART_TOP_N", 20)
    worker_poll_seconds: float = _as_float("REPORT_WORKER_POLL_SECONDS", 0.5)
    automation_enabled: bool = _as_bool("AUTOMATION_ENABLED", True)
    automation_scan_seconds: float = _as_float("AUTOMATION_SCAN_SECONDS", 15)
    email_retry_base_seconds: int = _as_int("EMAIL_RETRY_BASE_SECONDS", 60)
    smtp_enabled: bool = _as_bool("SMTP_ENABLED", False)
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = _as_int("SMTP_PORT", 587)
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", "")
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "切割生产智能产报系统")
    smtp_use_tls: bool = _as_bool("SMTP_USE_TLS", True)
    smtp_use_ssl: bool = _as_bool("SMTP_USE_SSL", False)
    smtp_timeout: float = _as_float("SMTP_TIMEOUT_SECONDS", 20)
    sql_workbench_enabled: bool = _as_bool("SQL_WORKBENCH_ENABLED", False)
    sql_workbench_admin_token: str = os.getenv("SQL_WORKBENCH_ADMIN_TOKEN", "")
    sql_workbench_max_rows: int = _as_int("SQL_WORKBENCH_MAX_ROWS", 200)
    auth_session_hours: int = _as_int("AUTH_SESSION_HOURS", 12)
    auth_cookie_secure: bool = _as_bool("AUTH_COOKIE_SECURE", False)

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
        return mode if mode in {"demo", "excel", "sqlserver"} else "demo"

    @property
    def task_excel_path(self) -> Path:
        path = Path(self.task_excel_file).expanduser()
        return path if path.is_absolute() else self.project_root / path

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


settings = Settings()
