import type { ApiError } from '../types/api'

export function toError(value: unknown): ApiError {
  return value instanceof Error ? value : new Error(String(value))
}
