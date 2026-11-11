"""Configuration response schemas; secrets are represented only by presence flags."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, JsonValue, Field, SecretStr, field_validator


class LlmCatalogRequest(BaseModel):
    base_url: str = Field(max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            parsed.port
        except ValueError:
            raise ValueError("请输入有效的模型接口地址") from None
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or any(character.isspace() for character in value)
                or any(ord(character) < 32 for character in value)):
            raise ValueError("请输入有效的模型接口地址")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and any(character in value.get_secret_value() for character in ("\r", "\n", "\x00")):
            raise ValueError("接口密钥不能包含换行或空字符")
        return value


class LlmConfigurationRequest(LlmCatalogRequest):
    enabled: bool
    model: str = Field(max_length=256)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        value = value.strip()
        if any(ord(character) < 32 for character in value):
            raise ValueError("模型 ID 不能包含控制字符")
        return value


class LlmConfiguration(BaseModel):
    enabled: bool
    base_url: str
    model: str
    api_key_configured: bool
    active_enabled: bool
    active_base_url: str
    active_model: str
    restart_required: bool


class LlmModelCatalog(BaseModel):
    models: list[str]


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
    welding: WeldingConnection | None


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
    scheduler: DiagnosticProcess | None = None
    automation: DiagnosticProcess | None = None
    consumers: dict[str, DiagnosticProcess] = Field(default_factory=dict)


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
    scheduler: WorkerConfigurationVersion | None = None
    automation: WorkerConfigurationVersion | None = None
    consumers: dict[str, WorkerConfigurationVersion] = Field(default_factory=dict)


class ConfigurationDiagnostics(BaseModel):
    categories: list[str]
    items: list[DiagnosticItem]
    errors: list[str]
    precedence: str
    processes: ConfigurationProcesses
    consistent: bool
    notes: list[str]
