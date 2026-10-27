import type { UsageReport } from '../types/usage'
import type { SystemHealth } from '../types/system'
import type { ReadOptions } from '../types/api'
import { request } from './client.ts'

export function getSystemHealth() {
  return request<SystemHealth>('/api/v1/health')
}

export function getLlmUsage() {
  return request('/api/v1/llm-usage')
}

export function getLlmUsageReport({ startDate, endDate, granularity = 'day', scene = '' }: { startDate: string; endDate: string; granularity?: string; scene?: string }, { signal }: ReadOptions = {}) {
  const query = new URLSearchParams({ start_date: startDate, end_date: endDate, granularity, scene })
  return request<UsageReport>(`/api/v1/llm-usage/report?${query}`, { signal })
}
