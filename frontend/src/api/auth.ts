import type { Items } from '../types/api'
import type { Authentication, AuthStatus, BootstrapRequest, HrPerson, LoginRequest, User } from '../types/auth'
import { request } from './client.ts'

export function getAuthStatus() {
  return request<AuthStatus>('/api/v1/auth/status')
}

export function bootstrapAccount(payload: BootstrapRequest) {
  return request<Authentication>('/api/v1/auth/bootstrap', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function loginAccount(payload: LoginRequest) {
  return request<Authentication>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function logoutAccount() {
  return request<{ logged_out: boolean }>('/api/v1/auth/logout', { method: 'POST' })
}

export function listUserAccounts() {
  return request<Items<User>>('/api/v1/auth/users')
}

export function searchHrPeople(query: string) {
  return request<Items<HrPerson>>(`/api/v1/auth/hr-directory?${new URLSearchParams({ query })}`)
}

export function listAdminAccounts() {
  return request<Items<User>>('/api/v1/auth/admins')
}

export function grantAdminAccess(externalId: string, currentPassword: string) {
  return request<User>('/api/v1/auth/admins', {
    method: 'POST',
    body: JSON.stringify({ external_id: externalId, current_password: currentPassword }),
  })
}

export function revokeAdminAccess(userId: string, currentPassword: string) {
  return request<User>(`/api/v1/auth/admins/${encodeURIComponent(userId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ current_password: currentPassword }),
  })
}
