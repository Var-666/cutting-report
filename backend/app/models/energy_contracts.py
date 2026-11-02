"""Equipment and metering JSON, including unavailable channels and optional range data."""
from pydantic import BaseModel, ConfigDict, JsonValue


class EnergyUnits(BaseModel):
    electricity: str
    gas: str


class EnergyChannel(BaseModel):
    __pydantic_extra__: dict[str, JsonValue]
    model_config = ConfigDict(extra='allow')
    value: float | None
    status: str
    label: str
    valid_count: int | None = None
    expected_count: int | None = None
    complete_count: int | None = None
    estimated_count: int | None = None


class EnergySummary(BaseModel):
    electricity: EnergyChannel
    gas: EnergyChannel


class EquipmentRecord(BaseModel):
    task_id: str
    equipment_id: str | None
    project_code: str | None
    ship_no: str | None
    segment_name: str | None
    cut_length: float | None
    work_time: float | None
    gas_consumption: float | None
    electricity_consumption: float | None
    production_time: str | None
    remark: str | None
    production_end_time: str | None
    gas_data_status: str
    electricity_data_status: str
    energy_note: str | None
    energy_details: dict[str, JsonValue]
    production_date: str | None


class EquipmentSummary(BaseModel):
    task_count: int
    total_work_time: float
    total_gas_consumption: float | None
    total_electricity_consumption: float | None
    gas_record_count: int
    electricity_record_count: int
    gas_missing_count: int
    electricity_missing_count: int
    estimated_record_count: int
    has_energy_data: bool
    energy_data_mode: str


class TaskEnergyReport(BaseModel):
    report_date: str
    records: list[EquipmentRecord]
    summary: EquipmentSummary
    units: EnergyUnits
    equipment_scope: list[str]
    source_task_count: int
    excluded_task_count: int
    energy_notice: str
    warning: str
    configuration_error: str | None
    energy_connected: bool
    period_start: str | None = None
    period_end: str | None = None
    data_updated_at: str | None = None
    analysis_source_version: str | None = None


class EnergyMetadata(BaseModel):
    units: EnergyUnits
    notice: str
    source_name: str
    timezone: str
    configuration_error: str | None
    energy_connected: bool
    equipment_scope: list[str]
    parameters: dict[str, str | list[str]]


class EnergyHour(EnergySummary):
    hour: int
    label: str
    start: str
    end: str


class EnergyDayReport(EnergyMetadata):
    report_date: str
    rows: list[EnergyHour]
    summary: EnergySummary
    updated_at: str | None
    saved: bool
    sync_ok: bool
    is_today: bool
    warning: str
    from_cache: bool | None = None


class EnergyCalendarDay(EnergySummary):
    report_date: str
    saved: bool
    updated_at: str | None
    is_future: bool
    is_today: bool
    needs_resync: bool
    task_count: int | None
    work_time_seconds: int | None


class EnergyMonthReport(EnergyMetadata):
    report_month: str
    rows: list[EnergyCalendarDay]
    summary: EnergySummary
    total_task_count: int
    total_work_time_seconds: int
    saved_day_count: int
    elapsed_day_count: int
    calendar_day_count: int
    resync_dates: list[str]
    storage_notice: str


class EnergyPeriodRow(EnergySummary):
    report_date: str
    task_count: int
    work_time_seconds: float
    is_future: bool | None = None
    is_today: bool | None = None
    saved: bool | None = None


class TaskEnergyPeriodReport(BaseModel):
    period_start: str
    period_end: str
    rows: list[EnergyPeriodRow]
    summary: EnergySummary
    total_task_count: int
    total_work_time_seconds: float
    saved_day_count: int
    elapsed_day_count: int
    calendar_day_count: int
    units: EnergyUnits
    equipment_scope: list[str]


class EnergyPeriodReport(TaskEnergyPeriodReport, EnergyMetadata):
    missing_day_count: int
    syncing: bool
    analysis_source_version: str


class EnergyRangeReport(BaseModel):
    task_report: TaskEnergyReport
    period_report: TaskEnergyPeriodReport


class EquipmentReport(BaseModel):
    report_date: str | None
    records: list[EquipmentRecord]
    summary: EquipmentSummary
    data_updated_at: str | None
    energy_notice: str
    units: EnergyUnits
    truncated: bool | None = None
