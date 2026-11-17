import { request } from './client'
import type { PdmBranchFilters, PdmBranchOptions, PdmReportSummaries, PdmStatisticsQuery, PdmStatisticsReport } from './generated/types.gen'
import type { PdmBranchFilters as FilterBranch, PdmDocumentKind, PdmFilters } from '../utils/pdmFilters'

export function apiBranch(branch: FilterBranch): PdmBranchFilters {
  const { responsiblePeople, ...fields } = branch
  return { ...fields, responsible_people: responsiblePeople }
}
export function pdmQuery(filters: PdmFilters): PdmStatisticsQuery {
  return { document_kinds: filters.documentKinds, start_date: filters.startDate, end_date: filters.endDate,
    group_by: filters.groupBy, date_basis: filters.dateBasis, equipment: apiBranch(filters.equipment), drawing: apiBranch(filters.drawing) }
}
export function getPdmOptions(kind: PdmDocumentKind, filters: FilterBranch, signal: AbortSignal) {
  return request<PdmBranchOptions>('/api/v1/pdm-statistics/options', { method: 'POST', body: JSON.stringify({ kind, filters: apiBranch(filters) }), signal })
}
export function getPdmReport(filters: PdmFilters, signal: AbortSignal) {
  return request<PdmStatisticsReport>('/api/v1/pdm-statistics/report', { method: 'POST', body: JSON.stringify(pdmQuery(filters)), signal })
}
export function getPdmOverview(signal: AbortSignal) {
  return request<PdmReportSummaries>('/api/v1/pdm-statistics/overview', { signal })
}
