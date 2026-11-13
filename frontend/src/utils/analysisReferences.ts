import type { BusinessReference } from '../api/generated/types.gen'

export function analysisReferenceLabel(reference: BusinessReference) {
  const label = reference.label?.trim()
  if (reference.kind !== 'task' || (label && label !== reference.id)) return label || reference.id || '—'
  const parts = [reference.program_no?.trim() ? `程序 ${reference.program_no.trim()}` : '',
    reference.project_code?.trim() ? `工程 ${reference.project_code.trim()}` : ''].filter(Boolean)
  if (parts.length) return parts.join(' · ')
  return `${reference.production_date ? `${reference.production_date} ` : ''}切割任务（业务信息未填写）`
}

export function analysisTaskLocation(reference: BusinessReference, currentDeviceId: string) {
  if (reference.kind !== 'task' || !reference.id || !reference.device_id || reference.device_id !== currentDeviceId
      || !reference.period_start || !reference.period_end) return null
  return { name: 'workshop-report', query: {
    start: reference.period_start, end: reference.period_end, task: reference.id, task_line: reference.device_id,
  } }
}
