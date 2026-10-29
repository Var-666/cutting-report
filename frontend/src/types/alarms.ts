import type { AlarmIncidentPage } from '../api/generated/types.gen'

export type {
  AlarmIncident, AlarmStatus, AlarmFact, AlarmAnalysis, AlarmReport,
  AlarmIncidentPage, AlarmReportPage,
} from '../api/generated/types.gen'
export type AlarmIncidentSummary = Omit<AlarmIncidentPage, 'items' | 'page' | 'page_size'>
