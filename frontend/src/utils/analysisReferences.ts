import type { BusinessReference } from '../api/generated/types.gen'

export function analysisTaskLocation(reference: BusinessReference, currentDeviceId: string) {
  if (reference.kind !== 'task' || !reference.id || !reference.device_id || reference.device_id !== currentDeviceId
      || !reference.period_start || !reference.period_end) return null
  return { name: 'workshop-report', query: {
    start: reference.period_start, end: reference.period_end, task: reference.id, task_line: reference.device_id,
  } }
}
