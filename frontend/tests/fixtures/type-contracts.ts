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
