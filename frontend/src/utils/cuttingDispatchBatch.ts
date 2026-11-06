import type { parseCuttingInstructionFilename } from './cuttingInstructionName'
import type { CuttingFile } from '../types/cutting'

export function cuttingFileKey(file: CuttingFile) {
  return JSON.stringify([file.document_id, file.version_id, file.normalized_path])
}

export function matchesDispatchParameters(current: { document_id?: string; version_id?: string | null; relative_path?: string; machine_ip?: string; material?: string | null; thickness?: number | string | null; quantity?: number | string } | null | undefined, item: { documentId: string; versionId?: string | null; filePath?: string; material?: string; thickness?: number | string; quantity?: number | string } | null | undefined, machineIp: string) {
  if (!current || !item) return false
  const normalizePath = (value: unknown) => String(value || '').replaceAll('\\', '/')
  return String(current.document_id) === item.documentId
    && String(current.version_id || '') === String(item.versionId || '')
    && normalizePath(current.relative_path) === normalizePath(item.filePath)
    && current.machine_ip === machineIp
    && String(current.material || '').trim().toLowerCase() === String(item.material || '').trim().toLowerCase()
    && Number(current.thickness) === Number(item.thickness)
    && Number(current.quantity) === Number(item.quantity)
}

export interface PushBatchItem { referenceKey: string; documentId: string; versionId: string | null; filePath: string; filename: string; version: string; parsed: ReturnType<typeof parseCuttingInstructionFilename>; segmentName: string; material: string; thickness: number | string; quantity: number; status: 'pending' | 'pushing' | 'sent' | 'duplicate' | 'failed'; error: string; dispatchId: string; retryable: boolean; statusChecked: boolean }

export function batchItemError(item: PushBatchItem) {
  const segment = String(item.segmentName || '').trim()
  if (!segment) return '请填写分段名'
  if (segment.length > 80 || /[\u0000-\u001f\u007f]/.test(segment)) return '分段名不能超过80字或包含控制字符'
  if (!String(item.material || '').trim()) return '请填写材质'
  const thickness = Number(item.thickness)
  if (!Number.isFinite(thickness) || thickness <= 0 || thickness > 1000) return '板厚必须大于 0 且不超过 1000 mm'
  const quantity = Number(item.quantity)
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) return '加工数量必须是 1–10000 的整数'
  return ''
}

export function hasNameMismatch(item: PushBatchItem) {
  if (!item.parsed) return false
  return String(item.material || '').trim().toLowerCase() !== item.parsed.material.toLowerCase()
    || Number(item.thickness) !== item.parsed.thickness
}

export function batchStatusLabel(item: PushBatchItem) {
  return {
    pending: '待创建',
    pushing: '下载并推送中',
    sent: '已推送',
    duplicate: '已有未完成任务',
    failed: '失败',
  }[item.status] || '待创建'
}

export function isStaleFileError(item: PushBatchItem) {
  return /版本已变化|路径已变化|PDM中未找到/.test(item.error)
}
