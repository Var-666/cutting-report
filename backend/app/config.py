from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


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
    sqlserver_query_timeout: int = _as_int("SQLSERVER_QUERY_TIMEOUT_SECONDS", 15)

    llm_enabled: bool = _as_bool("LLM_ENABLED", False)
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_timeout: float = _as_float("LLM_TIMEOUT_SECONDS", 30)

    report_max_detail_rows: int = _as_int("REPORT_MAX_DETAIL_ROWS", 1000)
    chart_top_n: int = _as_int("CHART_TOP_N", 20)
    worker_poll_seconds: float = _as_float("REPORT_WORKER_POLL_SECONDS", 0.5)
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

    def ensure_runtime_dirs(self) -> None:
        for path in (self.data_dir, self.reports_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
