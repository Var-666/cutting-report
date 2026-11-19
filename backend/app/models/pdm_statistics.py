"""PDM report contracts and the verified document type mapping."""
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.models.report_data import ReportMeta

PdmKind = Literal['equipment', 'drawing']
Selection = Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=2000)]

# Labels follow the supplied report; source names follow the onsite type export.
PDM_TYPES = {
    'equipment': {
        '设备技术协议-按设备统计': (7, '设备审批表', '技术协议-按设备计'),
        '设备技术协议-按单协议统计': (7, '设备技术协议', '技术协议-按协议计'),
        '设备认可资料': (8, '设备认可资料', '设备认可图'),
        '设备工作资料': (9, '设备工作资料', '设备工作资料'),
        '设备完工资料': (10, '设备完工资料', '设备完工资料'),
    },
    'drawing': {
        '预审图': (0, '详设预审图', '预审图'),
        '送审图': (0, '详设送审图', '送审图'),
        '船东退审图': (0, '详设船东退审图', '船东退审图'),
        '船检退审图': (0, '详设船检退审图', '船检退审图'),
        'O版图': (0, '详设O版图', 'O版图'),
        '详设下发图': (0, '详设图下发', '详设下发图'),
        '完工图': (0, '完工图', '完工图'),
    },
}


class PdmBranchFilters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    types: Selection = Field(default_factory=list)
    projects: Selection = Field(default_factory=list)
    professions: Selection = Field(default_factory=list)
    levels: Selection = Field(default_factory=list)
    areas: Selection = Field(default_factory=list)
    manufactory: Selection = Field(default_factory=list)
    batches: Selection = Field(default_factory=list)
    classifications: Selection = Field(default_factory=list)
    weights: Selection = Field(default_factory=list)
    responsible_people: Selection | None = None

    @model_validator(mode='after')
    def bounded_parameters(self):
        if sum(len(getattr(self, name) or []) for name in type(self).model_fields) > 1800:
            raise ValueError('筛选项过多，请缩小项目或专业范围')
        return self


class PdmOptionsQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: PdmKind
    filters: PdmBranchFilters = Field(default_factory=PdmBranchFilters)

    @model_validator(mode='after')
    def valid_types(self):
        if set(self.filters.types) - PDM_TYPES[self.kind].keys():
            raise ValueError('无效的资料类型')
        invalid = ('batches', 'classifications', 'weights') if self.kind == 'equipment' else ('levels', 'manufactory')
        if any(getattr(self.filters, field) for field in invalid):
            raise ValueError('筛选字段不适用于所选文档类型')
        return self


class PdmStatisticsQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    document_kinds: list[PdmKind] = Field(min_length=1, max_length=2)
    start_date: date
    end_date: date
    date_basis: Literal['plan', 'actual'] = 'plan'
    group_by: Literal['summary', 'project', 'profession'] = 'summary'
    equipment: PdmBranchFilters = Field(default_factory=PdmBranchFilters)
    drawing: PdmBranchFilters = Field(default_factory=PdmBranchFilters)

    @model_validator(mode='after')
    def valid_query(self):
        if self.end_date < self.start_date or self.end_date == date.max:
            raise ValueError('起止日期无效')
        if len(set(self.document_kinds)) != len(self.document_kinds):
            raise ValueError('文档类型不能重复')
        for kind in self.document_kinds:
            PdmOptionsQuery(kind=kind, filters=getattr(self, kind))
        return self


class PdmOption(BaseModel):
    value: str
    label: str


class PdmBranchOptions(BaseModel):
    projects: list[PdmOption] = Field(default_factory=list)
    professions: list[PdmOption] = Field(default_factory=list)
    levels: list[PdmOption] = Field(default_factory=list)
    areas: list[PdmOption] = Field(default_factory=list)
    manufactory: list[PdmOption] = Field(default_factory=list)
    batches: list[PdmOption] = Field(default_factory=list)
    classifications: list[PdmOption] = Field(default_factory=list)
    weights: list[PdmOption] = Field(default_factory=list)
    responsible_people: list[PdmOption] = Field(default_factory=list)
    meta: ReportMeta | None = None


class PdmCounts(BaseModel):
    total: int
    completed: int
    on_time: int
    total_rate: float | None
    on_time_rate: float | None


class PdmSummaryRow(PdmCounts):
    category: str
    group_id: str = ''
    group_name: str = ''
    subtotal: bool = False


class PdmSummaryTable(BaseModel):
    key: str
    kind: PdmKind
    title: str
    scope_label: str
    group_label: str | None
    rows: list[PdmSummaryRow]
    totals: PdmCounts


class PdmReportSummaries(BaseModel):
    equipment: PdmCounts | None = None
    drawing: PdmCounts | None = None


class PdmOverviewReport(PdmReportSummaries):
    meta: ReportMeta | None = None


class PdmStatisticsReport(BaseModel):
    start_date: date
    end_date: date
    date_basis: Literal['plan', 'actual'] = 'plan'
    group_by: Literal['summary', 'project', 'profession']
    summaries: PdmReportSummaries
    tables: list[PdmSummaryTable]
    meta: ReportMeta | None = None
