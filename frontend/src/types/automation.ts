export type { AutomationSchedule, AutomationStatus, EmailDelivery, EmailResolution, RecipientResult, SchedulePayload, DeliveryResolutionRequest, AutomationConfiguration } from '../api/generated/types.gen'
import type { RecipientResolution } from '../api/generated/types.gen'
export type RecipientOutcome = RecipientResolution['outcome']
