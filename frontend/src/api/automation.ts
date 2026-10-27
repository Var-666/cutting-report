import type { SaveResult } from '../types/configuration'
import type { AutomationConfiguration, AutomationSchedule, DeliveryResolutionRequest, AutomationStatus, EmailDelivery, SchedulePayload, EmailResolution } from '../types/automation'
import type { Items, Page, ReadOptions } from '../types/api'
import { request } from './client.ts'

export function getAutomationStatus() {
  return request<AutomationStatus>('/api/v1/automation/status')
}

export function getAutomationConfiguration() {
  return request<AutomationConfiguration>('/api/v1/automation/configuration')
}

export function saveAutomationConfiguration(payload: AutomationConfiguration) {
  return request<SaveResult>('/api/v1/automation/configuration', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function listAutomationSchedules() {
  return request<Items<AutomationSchedule>>('/api/v1/automation/schedules')
}

export function saveAutomationSchedule(payload: SchedulePayload, scheduleId = '') {
  const url = scheduleId
    ? `/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}`
    : '/api/v1/automation/schedules'
  return request<AutomationSchedule>(url, {
    method: scheduleId ? 'PUT' : 'POST',
    body: JSON.stringify(payload),
  })
}

export function deleteAutomationSchedule(scheduleId: string) {
  return request(`/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}`, {
    method: 'DELETE',
  })
}

export function runAutomationSchedule(scheduleId: string) {
  return request<{ report_id: string; status: string; last_error?: string }>(`/api/v1/automation/schedules/${encodeURIComponent(scheduleId)}/run`, {
    method: 'POST',
  })
}

export function listEmailDeliveries({ page = 1, pageSize = 20 } = {}) {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request<Page<EmailDelivery>>(`/api/v1/automation/deliveries?${query}`)
}

export function retryEmailDelivery(deliveryId: string) {
  return request(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/retry`, {
    method: 'POST',
  })
}

export function resolveEmailDelivery(deliveryId: string, payload: DeliveryResolutionRequest) {
  return request(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/resolve`, {
    method: 'POST', body: JSON.stringify(payload),
  })
}

export function listEmailDeliveryResolutions(deliveryId: string, { signal }: ReadOptions = {}) {
  return request<Items<EmailResolution>>(`/api/v1/automation/deliveries/${encodeURIComponent(deliveryId)}/resolutions`, { signal })
}
