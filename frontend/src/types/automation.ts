export type { AutomationSchedule, AutomationStatus, EmailResolution, RecipientResult, SchedulePayload, DeliveryResolutionRequest, AutomationConfiguration } from '../api/generated/types.gen'
import type { EmailDelivery as ApiEmailDelivery, RecipientResolution, ScheduledRun } from '../api/generated/types.gen'
export type EmailDelivery = Omit<ApiEmailDelivery, 'recipient_results'> & {
  recipient_results: Partial<ApiEmailDelivery['recipient_results']>
}
export type RecipientOutcome = RecipientResolution['outcome']

export type AutomationExecution = Pick<ScheduledRun, 'schedule_name' | 'status'>
  & Partial<Pick<ScheduledRun, 'run_id' | 'last_error'>> & {
  schedule_id: string
  monitoring_error?: string
  cancelling?: boolean
}
