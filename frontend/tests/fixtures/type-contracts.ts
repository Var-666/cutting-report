import type { UserRole } from '../../src/types/auth'
import type { ProductionLine, WeldingLine } from '../../src/types/devices'
import type { Page } from '../../src/types/api'
import type { ReportCreateRequest, ReportJob } from '../../src/types/reports'
import type { DeliveryResolutionRequest } from '../../src/types/automation'

const role: UserRole = 'worker'
// @ts-expect-error Roles cannot introduce an administrator alias.
const invalidRole: UserRole = 'superadmin'
const welding: WeldingLine = { id: 'welding', type: 'welding', name: '小组立焊接线', type_name: '焊接', enabled: true, capabilities: ['reports', 'analysis', 'settings'] }
// @ts-expect-error Welding lines do not have file dispatch capability.
const invalidWelding: WeldingLine = { ...welding, capabilities: ['dispatch'] }
function deviceLabel(line: ProductionLine) {
  if (line.type === 'welding') return line.enabled ? line.name : '未启用'
  return line.machine_no
}
const report: ReportCreateRequest = { report_type: 'workshop', report_date: '2026-10-27', parameters: { chart_type: 'bar_line' } }
// @ts-expect-error The report endpoint only accepts declared report types.
const invalidReport: ReportCreateRequest = { ...report, report_type: 'welding' }
function history(page: Page<ReportJob>) {
  const id: string | undefined = page.items[0]?.report_id
  // @ts-expect-error Report identifiers remain strings.
  const numericId: number = page.items[0].report_id
  return id
}
const resolution: DeliveryResolutionRequest = { message_id: 'mail-id', evidence_note: '已核对', outcomes: [{ recipient: 'worker@example.com', outcome: 'accepted' }] }
// @ts-expect-error An uncertain result cannot be submitted as a manual resolution.
resolution.outcomes.push({ recipient: 'worker@example.com', outcome: 'unknown' })
void [role, invalidRole, invalidWelding, deviceLabel, report, invalidReport, history, resolution]

import type { WeldingDailyRow, WeldingEquipmentRow, WeldingJobRow, WeldingPartRow, WeldingPart, WeldingQuery, WeldingFilterDraft, WeldingReport } from '../../src/types/welding'
import { analyzeWelding, getWeldingReport } from '../../src/api/welding'

const period = { start: '2026-10-01', end: '2026-10-02' }
const weldingSort: WeldingQuery = { ...period, sort_by: 'length' }
// @ts-expect-error Sort keys must match the backend whitelist.
weldingSort.sort_by = 'serial_number'
function weldingRows(daily: WeldingDailyRow, equipment: WeldingEquipmentRow, job: WeldingJobRow, part: WeldingPartRow, detail: WeldingPart) {
  const date: string = daily.production_date
  const robot: number | null = equipment.robot_no
  const id: number = job.id
  const serial: string | null = part.serial_number
  // @ts-expect-error Daily aggregates have no detail ID.
  daily.id
  // @ts-expect-error Equipment aggregates have no production date.
  equipment.production_date
  // @ts-expect-error Part records do not contain a robot number.
  part.robot_no
  // @ts-expect-error Seams are not job rows.
  const seamJob: WeldingJobRow = detail.items[0]
  return [date, robot, id, serial, seamJob]
}
function weldingReport(report: WeldingReport) {
  if (report.report_type === 'daily') {
    const daily: WeldingDailyRow[] = report.items
    // @ts-expect-error Daily reports cannot be treated as job details.
    const jobs: WeldingJobRow[] = report.items
    return [daily, jobs]
  }
  if (report.report_type === 'parts') {
    const parts: WeldingPartRow[] = report.items
    return parts
  }
}
async function weldingApi() {
  const daily: WeldingReport<'daily'> = await getWeldingReport('daily', period)
  const dashboard: WeldingReport<'jobs'> = await getWeldingReport('dashboard', period)
  // @ts-expect-error Endpoint-specific return types cannot be interchanged.
  const jobs: WeldingReport<'jobs'> = await getWeldingReport('equipment', period)
  // @ts-expect-error Requests cannot omit both dates.
  getWeldingReport('daily', {})
  // @ts-expect-error The end date is required.
  getWeldingReport('jobs', { start: period.start })
  // @ts-expect-error The start date is required.
  getWeldingReport('equipment', { end: period.end })
  const draft: WeldingFilterDraft = {}
  // @ts-expect-error An unsubmitted draft is not a complete API query.
  getWeldingReport('parts', draft)
  // @ts-expect-error Analysis must also include both dates.
  analyzeWelding('jobs', {}, 'version')
  return [daily, dashboard, jobs]
}
void [weldingRows, weldingReport, weldingSort, weldingApi]
