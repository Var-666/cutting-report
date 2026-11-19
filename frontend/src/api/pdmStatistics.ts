import { request } from './client'
import type { PdmBranchFilters, PdmBranchOptions, PdmOverviewReport, PdmStatisticsQuery, PdmStatisticsReport } from './generated/types.gen'
import { readReport, type ReportReadOptions } from './reportRequests'
import type { PdmBranchFilters as FilterBranch, PdmDocumentKind, PdmFilters } from '../utils/pdmFilters'

export function apiBranch(branch: FilterBranch): PdmBranchFilters {
  const { responsiblePeople, ...fields } = branch
  return { ...fields, responsible_people: responsiblePeople }
}
export function pdmQuery(filters: PdmFilters): PdmStatisticsQuery {
  return { document_kinds: filters.documentKinds, start_date: filters.startDate, end_date: filters.endDate,
    group_by: filters.groupBy, date_basis: filters.dateBasis, equipment: apiBranch(filters.equipment), drawing: apiBranch(filters.drawing) }
}
function normalized(value: unknown): unknown {
  if (Array.isArray(value)) return [...new Set(value)].sort()
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => [key, normalized(item)]))
  return value
}
export function getPdmOptions(kind: PdmDocumentKind, filters: FilterBranch, signal: AbortSignal, options: Omit<ReportReadOptions, 'signal'> = {}) {
  const body = JSON.stringify(normalized({ kind, filters: { types: filters.types, projects: filters.projects, professions: filters.professions } }))
  return readReport(`pdm:options:${body}`, (sharedSignal, refresh) => request<PdmBranchOptions>(`/api/v1/pdm-statistics/options${refresh ? '?refresh=true' : ''}`, {
    method: 'POST', body, signal: sharedSignal,
  }), { ...options, signal, cacheScope: '@pdm-statistics' })
}
export function getPdmReport(filters: PdmFilters, signal: AbortSignal, options: Omit<ReportReadOptions, 'signal'> = {}) {
  const values = pdmQuery(filters)
  for (const kind of ['equipment', 'drawing'] as const) if (!values.document_kinds.includes(kind)) values[kind] = {}
  const body = JSON.stringify(normalized(values))
  return readReport(`pdm:report:${body}`, (sharedSignal, refresh) => request<PdmStatisticsReport>(`/api/v1/pdm-statistics/report${refresh ? '?refresh=true' : ''}`, {
    method: 'POST', body, signal: sharedSignal,
  }), { ...options, signal, cacheScope: '@pdm-statistics' })
}
export function getPdmOverview(signal: AbortSignal, options: Omit<ReportReadOptions, 'signal'> = {}) {
  return readReport('pdm:overview', (sharedSignal, refresh) => request<PdmOverviewReport>(`/api/v1/pdm-statistics/overview${refresh ? '?refresh=true' : ''}`, {
    signal: sharedSignal,
  }), { ...options, signal, cacheScope: '@pdm-statistics' })
}
