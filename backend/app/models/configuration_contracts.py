"""Configuration response schemas; secrets are represented only by presence flags."""
from typing import Literal

from pydantic import BaseModel, JsonValue


class SavedConnection(BaseModel):
    host: str
    port: int
    database: str
    username: str
    password_configured: bool
    driver: str
    encrypt: bool
    trust_server_certificate: bool


class WeldingConnection(BaseModel):
    enabled: bool
    host: str
    port: int
    database: str
    username: str
    password_configured: bool
    timezone: str


class DatabaseConfiguration(BaseModel):
    data_source_mode: Literal['demo', 'sqlserver']
    worker_database_auth_enabled: bool
    file_database_enabled: bool
    production: SavedConnection
    worker: SavedConnection
    file: SavedConnection
    welding: WeldingConnection


class ConfigurationSaved(BaseModel):
    saved: bool
    restart_required: bool
    path: str
    message: str


class ConnectionTestResult(BaseModel):
    connected: bool
    elapsed_ms: float
    message: str
    database: str | None = None
    saved: bool | None = None


class DatabaseSourceStatus(BaseModel):
    source: str
    label: str
    enabled: bool
    configured: bool
    host: str | None
    port: int
    database: str | None
    driver: str
    uses_primary_fallback: bool


class QueryMapping(BaseModel):
    dataset: str
    label: str
    filename: str
    ready: bool


class DatabaseStatus(BaseModel):
    mode: Literal['demo', 'sqlserver']
    demo_mode: bool
    pyodbc_installed: bool
    connection_configured: bool
    configured_host: str | None
    configured_database: str | None
    driver: str
    data_sources: list[DatabaseSourceStatus]
    query_mappings: list[QueryMapping]
    all_queries_ready: bool
    arbitrary_sql_enabled: bool
    sql_workbench_enabled: bool
    sql_workbench_requires_token: bool
    sql_workbench_token_configured: bool
    sql_workbench_max_rows: int


class DatabasePreview(BaseModel):
    dataset: Literal['summary', 'workshop', 'equipment']
    mode: str
    items: list[dict[str, JsonValue]]
    returned: int
    total: int
    truncated: bool


class ValidatedWorkbenchSql(BaseModel):
    valid: bool
    formatted_sql: str
    tables: list[str]
    columns: list[str]
    read_only: bool


class WorkbenchQueryResult(BaseModel):
    columns: list[str]
    items: list[dict[str, JsonValue]]
    returned: int
    truncated: bool
    elapsed_ms: float


DiagnosticStatus = Literal['effective', 'pending', 'error', 'unconfigured']


class DiagnosticProcess(BaseModel):
    value: JsonValue
    source: str
    matches_saved: bool
    status: DiagnosticStatus


class DiagnosticProcesses(BaseModel):
    web: DiagnosticProcess
    worker: DiagnosticProcess


class DiagnosticItem(BaseModel):
    key: str
    category: str
    processes: DiagnosticProcesses
    saved_value: JsonValue
    saved_source: str
    status: DiagnosticStatus
    valid: bool
    process_mismatch: bool
    restart_required: bool
    rule: str


class ConfigurationProcessVersion(BaseModel):
    loaded_at: str
    hot_loaded_at: str
    hot_revision: int
    version: str


class WorkerConfigurationVersion(BaseModel):
    state: str
    available: bool
    loaded_at: str | None = None
    hot_loaded_at: str | None = None
    hot_revision: int | None = None
    version: str | None = None


class ConfigurationProcesses(BaseModel):
    web: ConfigurationProcessVersion
    worker: WorkerConfigurationVersion


class ConfigurationDiagnostics(BaseModel):
    categories: list[str]
    items: list[DiagnosticItem]
    errors: list[str]
    precedence: str
    processes: ConfigurationProcesses
    consistent: bool
    notes: list[str]
