export type { AutomationSchedule, AutomationStatus, EmailDelivery, EmailResolution, RecipientResult, SchedulePayload, DeliveryResolutionRequest, AutomationConfiguration } from '../api/generated/types.gen'
import type { RecipientResolution, ScheduledRun } from '../api/generated/types.gen'
export type RecipientOutcome = RecipientResolution['outcome']

export type AutomationExecution = Pick<ScheduledRun, 'schedule_name' | 'status'>
  & Partial<Pick<ScheduledRun, 'run_id' | 'last_error'>> & {
  schedule_id: string
  monitoring_error?: string
}
