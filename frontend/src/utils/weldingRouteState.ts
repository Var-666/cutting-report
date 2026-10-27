import type { LocationQuery } from 'vue-router'
import type { WeldingQuery, WeldingReportKind } from '../types/welding'
import { mergeOwnedQuery, queryChoice, queryDateRange } from './reportRouteState.ts'

type WeldingRouteState = { [Key in keyof WeldingQuery]-?: NonNullable<WeldingQuery[Key]> }
const fields = ['start', 'end', 'device_no', 'robot_no', 'weld_type', 'state', 'part_state', 'serial_number', 'project_no', 'section_no', 'part_no', 'anomalies_only', 'sort_by', 'descending', 'page', 'page_size']
const scalar = (value: unknown) => typeof value === 'string' ? value : ''
function integer(value: unknown, minimum: number, maximum = Number.MAX_SAFE_INTEGER) {
  const text = scalar(value)
  if (!/^-?\d+$/.test(text)) return ''
  const number = Number(text)
  return Number.isSafeInteger(number) && number >= minimum && number <= maximum ? number : ''
}
function text(value: unknown) {
  const result = scalar(value).trim()
  return result.length <= 255 && !/[\u0000-\u001f]/.test(result) ? result : ''
}

export function parseWeldingRouteQuery(query: LocationQuery, kind: WeldingReportKind, today: string): WeldingRouteState {
  const weekStart = new Date(Date.parse(`${today}T00:00:00Z`) - 6 * 86400000).toISOString().slice(0, 10)
  const range = queryDateRange({ start: scalar(query.start), end: scalar(query.end) })
  const result: WeldingRouteState = {
    start: range?.start || (kind === 'dashboard' ? weekStart : today), end: range?.end || today,
    device_no: integer(query.device_no, 0), robot_no: '', weld_type: '', state: '', part_state: '',
    serial_number: '', project_no: '', section_no: '', part_no: '', anomalies_only: false,
    sort_by: queryChoice(query.sort_by, ['time', 'length', 'id'] as const, 'time'),
    descending: scalar(query.descending) !== 'false',
    page: integer(query.page, 1, 100000) || 1, page_size: integer(query.page_size, 1, 100) || 20,
  }
  if (kind === 'jobs' || kind === 'equipment') {
    result.robot_no = integer(query.robot_no, 0)
    result.weld_type = integer(query.weld_type, Number.MIN_SAFE_INTEGER)
  }
  if (kind === 'jobs' || kind === 'parts') {
    result.serial_number = text(query.serial_number)
  }
  if (kind === 'jobs') result.state = integer(query.state, Number.MIN_SAFE_INTEGER)
  if (kind === 'parts') {
    result.part_state = integer('part_state' in query ? query.part_state : query.state, Number.MIN_SAFE_INTEGER)
    result.project_no = text(query.project_no)
    result.section_no = text(query.section_no)
    result.part_no = text(query.part_no)
  }
  if (kind === 'jobs') result.anomalies_only = scalar(query.anomalies_only) === 'true'
  if (kind === 'dashboard') Object.assign(result, { page: 1, page_size: 10, sort_by: 'time', descending: true })
  return result
}

export function weldingRouteQuery(current: LocationQuery, values: WeldingQuery) {
  return mergeOwnedQuery(current, fields, Object.fromEntries(fields.map(key => [key, values[key as keyof WeldingQuery]])))
}
