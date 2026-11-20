import { queryDate } from './reportRouteState'
import type { PdmBranchFilters as ApiBranchFilters, PdmBranchOptions, PdmStatisticsQuery } from '../api/generated/types.gen'

export const pdmDocumentKinds = [
  { value: 'equipment', label: '设备资料' },
  { value: 'drawing', label: '详设图纸' },
] as const
export type PdmDocumentKind = typeof pdmDocumentKinds[number]['value']
export type PdmFilterField = 'types' | 'projects' | 'professions' | 'levels' | 'areas' | 'manufactory' | 'batches' | 'classifications' | 'weights' | 'responsiblePeople'
export type PdmOption = { value: string; label: string }
export type PdmBranchFilters = Required<Omit<ApiBranchFilters, 'responsible_people'>> & { responsiblePeople: string[] | null }
export interface PdmFilters {
  documentKinds: PdmDocumentKind[]
  groupBy: 'summary' | 'project' | 'profession'
  dateBasis: NonNullable<PdmStatisticsQuery['date_basis']>
  startDate: string
  endDate: string
  equipment: PdmBranchFilters
  drawing: PdmBranchFilters
}
export type PdmFilterOptions = Partial<Record<PdmDocumentKind, PdmBranchOptions>>
export type PdmPreferences = Record<PdmDocumentKind, Pick<PdmBranchFilters, 'projects' | 'professions'>>

const typeOptions = (labels: string[]): PdmOption[] => labels.map(label => ({ value: label, label }))
export const pdmTypeOptions: Record<PdmDocumentKind, PdmOption[]> = {
  equipment: typeOptions(['设备技术协议-按设备统计', '设备技术协议-按单协议统计', '设备认可资料', '设备工作资料', '设备完工资料']),
  drawing: typeOptions(['预审图', '送审图', '船东退审图', '船检退审图', 'O版图', '详设下发图', '完工图']),
}
export const pdmFilterFields: Record<PdmDocumentKind, { key: PdmFilterField; label: string }[]> = {
  equipment: [
    { key: 'types', label: '资料类型' }, { key: 'projects', label: '项目' }, { key: 'professions', label: '专业' },
    { key: 'levels', label: '设备等级' }, { key: 'areas', label: '设备区域' },
    { key: 'manufactory', label: '是否厂商表' }, { key: 'responsiblePeople', label: '负责人' },
  ],
  drawing: [
    { key: 'types', label: '详设类型' }, { key: 'projects', label: '项目' }, { key: 'professions', label: '专业' },
    { key: 'batches', label: '图纸批次' }, { key: 'areas', label: '图纸区域' },
    { key: 'classifications', label: '图纸类别' }, { key: 'weights', label: '权重' },
    { key: 'responsiblePeople', label: '负责人' },
  ],
}

function rememberedBranch(value: unknown): Pick<PdmBranchFilters, 'projects' | 'professions'> {
  const record = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  const strings = (items: unknown) => Array.isArray(items) ? [...new Set(items.filter((item): item is string => typeof item === 'string' && item.length > 0))] : []
  return { projects: strings(record.projects), professions: strings(record.professions) }
}

export function pdmCurrentMonth(now = new Date()) {
  const date = now.toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
  const [year, month] = date.split('-').map(Number)
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate()
  return { startDate: `${date.slice(0, 7)}-01`, endDate: `${date.slice(0, 7)}-${lastDay}` }
}

export function createPdmFilters(preferences?: unknown): PdmFilters {
  const saved = preferences && typeof preferences === 'object' ? preferences as Record<string, unknown> : {}
  const branch = (kind: PdmDocumentKind): PdmBranchFilters => ({
    types: [], levels: [], areas: [], manufactory: [], batches: [], classifications: [], weights: [], responsiblePeople: null,
    ...rememberedBranch(saved[kind]),
  })
  return { documentKinds: ['equipment', 'drawing'], groupBy: 'summary', dateBasis: 'plan', ...pdmCurrentMonth(), equipment: branch('equipment'), drawing: branch('drawing') }
}

export function pdmPreferences(filters: PdmFilters): PdmPreferences {
  return { equipment: rememberedBranch(filters.equipment), drawing: rememberedBranch(filters.drawing) }
}

export function clonePdmFilters(filters: PdmFilters): PdmFilters {
  const branch = (value: PdmBranchFilters): PdmBranchFilters => ({ ...value,
    types: [...value.types], projects: [...value.projects], professions: [...value.professions], levels: [...value.levels],
    areas: [...value.areas], manufactory: [...value.manufactory], batches: [...value.batches],
    classifications: [...value.classifications], weights: [...value.weights], responsiblePeople: value.responsiblePeople === null ? null : [...value.responsiblePeople],
  })
  return { ...filters, documentKinds: [...filters.documentKinds], equipment: branch(filters.equipment), drawing: branch(filters.drawing) }
}

export function validatePdmFilters(filters: PdmFilters): string {
  if (!filters.documentKinds.length) return '请至少选择一种文档类型'
  if (!queryDate(filters.startDate) || !queryDate(filters.endDate)) return '请选择或输入完整的起止日期'
  if (filters.endDate < filters.startDate) return '结束日期不能早于开始日期'
  for (const kind of filters.documentKinds) {
    const count = Object.values(filters[kind]).reduce((total, values) => total + new Set(values).size, 0)
    if (count > 1800) return '筛选项超过 1800 项，请缩小筛选范围'
  }
  return ''
}
