"""Welding wire data; raw source columns and normalized units both remain visible."""
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from app.models.welding import WeldingQuery


class ReportDataMeta(BaseModel):
    line_id: str
    report_type: str
    data_version: str
    data_as_of: str
    refreshed_at: str
    expires_at: str
    source: Literal['snapshot', 'cache', 'database']
    stale: bool
    refreshing: bool
    refresh_failed: bool
    details_source: Literal['snapshot', 'cache', 'database'] | None = None
    today_data_as_of: str | None = None


class WeldingMetrics(BaseModel):
    source_rows: int
    job_count: int
    involved_part_count: int
    weld_length_m: float
    working_hours: float
    output_per_hour: float | None
    device_count: int
    robot_station_count: int
    invalid_time_rows: int
    invalid_serial_rows: int
    invalid_length_rows: int
    invalid_duration_rows: int
    unverified_state_rows: int
    flat_wrap_length_m: float
    flat_length_m: float
    vertical_wrap_length_m: float
    vertical_length_m: float
    unknown_length_m: float


class WeldingDisplayMetrics(WeldingMetrics):
    wrap_length_m: float
    completed_part_count: None
    arc_rate: None
    weld_rate: None
    additional_wrap_length_m: None


class WeldingSummary(WeldingDisplayMetrics):
    part_row_count: int | None = None
    part_actual_length_m: float | None = None
    ambiguous_part_rows: int | None = None
    invalid_part_rows: int | None = None


class WeldingDailyRow(WeldingDisplayMetrics):
    production_date: str


class WeldingDeviceRow(WeldingMetrics):
    device_no: int | None


class WeldingEquipmentRow(WeldingDisplayMetrics):
    device_no: int | None
    robot_no: int | None


class WeldingDetailRow(BaseModel):
    id: int
    serial_number: str | None
    device_no: int | None
    start_time: str | None
    end_time: str | None
    state: int | None
    update_date: str | None
    used_time: float | None
    working_hours: float | None
    state_label: str
    quality_reasons: list[str]


class WeldingJobRow(WeldingDetailRow):
    robot_no: int | None
    riser_no: int | None
    welding_seam_no: int | None
    weld_task_serial_number: int | None
    weld_type: int | None
    weld_speed: float | None
    weld_lenght: float | None
    wrap_angle_lenght: float | None
    weld_type_label: str
    weld_length_m: float | None
    additional_wrap_length_m: float | None


class WeldingPartRow(WeldingDetailRow):
    project_no: str | None
    project_name: str | None
    section_no: str | None
    part_no: str | None
    theory_lenght: float | None
    reality_lenght: float | None
    weld_rate: float | None
    arc_rate: float | None
    weld_used_time: float | None
    theoretical_length_m: float | None
    actual_length_m: float | None


class WeldingSeamRow(WeldingDetailRow):
    robot_no: int | None
    riser_no: int | None
    welding_seam_no: int | None
    theory_lenght: float | None
    reality_lenght: float | None
    theoretical_length_m: float | None
    actual_length_m: float | None


class WeldingMeta(BaseModel):
    line_key: str
    timezone: str
    calculated_at: str
    data_updated_at: dict[str, str | None]
    devices: list[int]
    units: dict[str, str]
    pending_metrics: dict[str, str]
    quality: dict[str, int]
    unlinked_part_count: int
    ambiguous_part_count: int
    notices: list[str]
    report_data: ReportDataMeta | None = None
    source_info_as_of: str | None = None


class WeldingProject(BaseModel):
    project_name: str | None
    project_no: str | None
    cumulative_actual_length_m: float | None
    part_count: int


class WeldingSpeedBucket(BaseModel):
    speed_band: str
    job_count: int


Row = TypeVar('Row')


class WeldingReport(BaseModel, Generic[Row]):
    query: WeldingQuery
    summary: WeldingSummary
    today: WeldingDisplayMetrics | None
    daily: list[WeldingDailyRow]
    equipment: list[WeldingEquipmentRow]
    device_distribution: list[WeldingDeviceRow]
    projects: list[WeldingProject]
    projects_truncated: bool
    speed_distribution: list[WeldingSpeedBucket]
    items: list[Row]
    total: int
    meta: WeldingMeta
    data_version: str


class WeldingDailyReport(WeldingReport[WeldingDailyRow]):
    report_type: Literal['daily']


class WeldingJobsReport(WeldingReport[WeldingJobRow]):
    report_type: Literal['jobs']


class WeldingPartsReport(WeldingReport[WeldingPartRow]):
    report_type: Literal['parts']


class WeldingEquipmentReport(WeldingReport[WeldingEquipmentRow]):
    report_type: Literal['equipment']


class WeldingPart(BaseModel):
    part: WeldingPartRow
    items: list[WeldingSeamRow]
    total: int
    association_status: str
    notice: str


class WeldingHealth(BaseModel):
    connected: bool
    devices: list[int]
    updated_at: dict[str, str | None]
