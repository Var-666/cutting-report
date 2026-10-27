import type { AlarmReport } from '../types/alarms'
function reportTime(report: AlarmReport) {
  const value = Date.parse(report?.completed_at || report?.created_at || '')
  return Number.isFinite(value) ? value : 0
}

function hasDisplayableAnalysis(report: AlarmReport) {
  const analysis = report?.analysis
  return report?.status === 'completed'
    && report?.model_status === 'model'
    && analysis
    && typeof analysis.summary === 'string'
    && Array.isArray(analysis.findings)
    && Array.isArray(analysis.actions)
    && Array.isArray(analysis.facts)
    && Array.isArray(analysis.limitations)
}

export function latestModelAlarmReport(reports: AlarmReport[]) {
  return (Array.isArray(reports) ? reports : [])
    .filter(hasDisplayableAnalysis)
    .sort((left, right) => reportTime(right) - reportTime(left))[0] || null
}

export function alarmConfidencePercent(value: unknown) {
  const number = Number(value)
  if (!Number.isFinite(number) || number < 0 || number > 1) return null
  return Math.round(number * 100)
}
