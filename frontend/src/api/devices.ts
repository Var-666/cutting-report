import type { DeviceCatalog } from '../types/devices'
import { request } from './client.ts'

export function getDeviceCatalog() { return request<DeviceCatalog>('/api/v1/devices') }
export function getProductionLines() { return request<DeviceCatalog>('/api/v1/production-lines') }
