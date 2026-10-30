import type { Ref } from 'vue'
import type { CuttingFile, CuttingStatus, DispatchCreationResult } from '../types/cutting'
import { computed, reactive, ref } from 'vue'
import { createCuttingDispatchBatch, executeCuttingDispatch, getCuttingDispatch } from '../api/dispatch'
import { parseCuttingInstructionFilename } from '../utils/cuttingInstructionName'
import { cuttingFileKey as fileKey, matchesDispatchParameters } from '../utils/cuttingDispatchBatch'
import { toError } from '../utils/errors'

export function useCuttingDispatchBatch({ selectedFiles, clearSelectedFiles, dispatchForm, systemStatus, operatingId, loadHistory, refreshVisibleFileStatuses, loadFiles, showNotice }: {
  selectedFiles: Ref<Map<string, CuttingFile>>
  clearSelectedFiles: () => void
  dispatchForm: { machineIp: string }
  systemStatus: Ref<CuttingStatus | null>
  operatingId: Ref<string>
  loadHistory: () => Promise<void>
  refreshVisibleFileStatuses: () => Promise<void>
  loadFiles: (page?: number) => Promise<void>
  showNotice: (message: string, type?: string) => void
}) {
  interface PushBatchItem { referenceKey: string; documentId: string; versionId: string | null; filePath: string; filename: string; version: string; parsed: ReturnType<typeof parseCuttingInstructionFilename>; segmentName: string; material: string; thickness: number | string; quantity: number; status: 'pending' | 'pushing' | 'sent' | 'duplicate' | 'failed'; error: string; dispatchId: string; retryable: boolean; statusChecked: boolean }
  const bulkQuantity = ref(1)
  const pushBatch = reactive({
    visible: false,
    saving: false,
    resultsReady: false,
    items: [] as PushBatchItem[],
    completed: 0,
    total: 0,
    currentFilename: '',
    error: '',
    nonStandardConfirmed: false,
    overrideConfirmed: false,
  })
  const pushBatchOpen = computed(() => pushBatch.visible)
  const invalidBatchCount = computed(() => pushBatch.items.filter((item) => batchItemError(item)).length)
  const nonStandardBatchCount = computed(() => pushBatch.items.filter((item) => !item.parsed).length)
  const overrideBatchCount = computed(() => pushBatch.items.filter((item) => hasNameMismatch(item)).length)
  const batchStatusCounts = computed(() => {
    const counts: Record<string, number> = { sent: 0, duplicate: 0, failed: 0, pending: 0 }
    pushBatch.items.forEach((item) => {
      const key = Object.hasOwn(counts, item.status) ? item.status : 'pending'
      counts[key] += 1
    })
    return counts
  })

  function createPushBatchItem(file: CuttingFile): PushBatchItem {
    const filename = file.document_no || file.physical_file_name || ''
    const parsed = parseCuttingInstructionFilename(filename)
    return {
      referenceKey: fileKey(file),
      documentId: String(file.document_id),
      versionId: file.version_id || null,
      filePath: file.normalized_path || '',
      filename,
      version: String(file.version || file.physical_version_code?.trim() || file.physical_version_no || '--'),
      parsed,
      segmentName: parsed?.intermediateProduct || '',
      material: parsed?.material || '',
      thickness: parsed?.thickness ?? '',
      quantity: 1,
      status: 'pending',
      error: '',
      dispatchId: '',
      retryable: false,
      statusChecked: false,
    }
  }

  function batchItemError(item: PushBatchItem) {
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

  function hasNameMismatch(item: PushBatchItem) {
    if (!item.parsed) return false
    return String(item.material || '').trim().toLowerCase() !== item.parsed.material.toLowerCase()
      || Number(item.thickness) !== item.parsed.thickness
  }

  function openPushBatch() {
    if (!selectedFiles.value.size) {
      showNotice('请先选择要推送的文件。', 'error')
      return
    }
    pushBatch.items = [...selectedFiles.value.values()].map(createPushBatchItem)
    pushBatch.completed = 0
    pushBatch.total = pushBatch.items.length
    pushBatch.currentFilename = ''
    pushBatch.error = ''
    pushBatch.nonStandardConfirmed = false
    pushBatch.overrideConfirmed = false
    pushBatch.resultsReady = false
    bulkQuantity.value = 1
    pushBatch.visible = true
  }

  function closePushBatch() {
    if (pushBatch.saving) return
    pushBatch.visible = false
  }

  function removePushBatchItem(item: PushBatchItem) {
    if (pushBatch.saving || pushBatch.resultsReady) return
    pushBatch.items = pushBatch.items.filter((candidate) => candidate.referenceKey !== item.referenceKey)
    const next = new Map(selectedFiles.value)
    next.delete(item.referenceKey)
    selectedFiles.value = next
    pushBatch.total = pushBatch.items.length
    if (!pushBatch.items.length) closePushBatch()
  }

  function applyBulkQuantity() {
    const quantity = Number(bulkQuantity.value)
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) {
      pushBatch.error = '统一数量必须是 1–10000 的整数。'
      return
    }
    pushBatch.items.forEach((item) => { item.quantity = quantity })
    if (nonStandardBatchCount.value) pushBatch.nonStandardConfirmed = false
    pushBatch.error = ''
  }

  function invalidateBatchConfirmation(item: PushBatchItem) {
    if (item.parsed) pushBatch.overrideConfirmed = false
    else pushBatch.nonStandardConfirmed = false
  }

  function batchRequestItem(item: PushBatchItem) {
    return {
      document_id: item.documentId,
      version_id: item.versionId,
      file_path: item.filePath,
      material: String(item.material).trim(),
      thickness: Number(item.thickness),
      quantity: Number(item.quantity),
      name_check_confirmed: item.parsed ? hasNameMismatch(item) && pushBatch.overrideConfirmed : pushBatch.nonStandardConfirmed,
    }
  }

  function batchStatusLabel(item: PushBatchItem) {
    return {
      pending: '待创建',
      pushing: '下载并推送中',
      sent: '已推送',
      duplicate: '已有未完成任务',
      failed: '失败',
    }[item.status] || '待创建'
  }

  function applyCreationResult(item: PushBatchItem, result: DispatchCreationResult | undefined) {
    item.dispatchId = result?.dispatch?.dispatch_id || ''
    item.retryable = false
    item.statusChecked = false
    if (result?.status === 'created' && item.dispatchId) {
      item.status = 'pending'
      item.error = ''
      return true
    }
    item.status = result?.status === 'duplicate' ? 'duplicate' : 'failed'
    item.error = result?.error || '任务创建结果不完整，请核对派发记录后重试'
    return false
  }

  function isStaleFileError(item: PushBatchItem) {
    return /版本已变化|路径已变化|PDM中未找到/.test(item.error)
  }

  async function syncBatchItem(item: PushBatchItem) {
    const current = await getCuttingDispatch(item.dispatchId)
    const matching = matchesDispatchParameters(current, item, dispatchForm.machineIp)
    item.statusChecked = true
    item.retryable = Boolean(current.can_execute) && matching
    if (!matching) {
      item.error = '已有任务的文件版本、机床或加工参数不一致，请在派发记录中核对，不要直接继续'
    } else if (current.status === 'sent') {
      item.status = 'sent'
      item.error = ''
    } else if (!current.can_execute) {
      item.error = `${current.last_error || current.status_label}；${current.stage_label}，请核对派发记录和魔盒状态`
    } else if (item.status === 'duplicate') {
      item.error = '已有同参数任务，可以核对后继续执行'
    } else {
      item.status = 'failed'
      item.error = current.last_error || '当前任务可安全重试'
    }
    return current
  }

  async function executeBatchItem(item: PushBatchItem) {
    item.status = 'pushing'
    item.retryable = false
    item.error = ''
    pushBatch.currentFilename = item.filename
    operatingId.value = item.dispatchId
    try {
      const completed = await executeCuttingDispatch(item.dispatchId, item.segmentName)
      item.status = 'sent'
      if (completed.local_file_cleanup?.error) item.error = completed.local_file_cleanup.error
    } catch (caught) {
      const error = toError(caught)
      item.status = 'failed'
      item.error = error.message
      try {
        await syncBatchItem(item)
      } catch {
        item.error = `${error.message}；任务状态未知，请先核对状态，不要重复上传`
      }
    } finally {
      operatingId.value = ''
    }
  }

  async function refreshBatchHistory() {
    await Promise.allSettled([loadHistory(), refreshVisibleFileStatuses()])
    const counts = batchStatusCounts.value
    const cleanupWarnings = pushBatch.items.filter((item) => item.status === 'sent' && item.error).length
    showNotice(
      `文件派发结果：成功 ${counts.sent} 项，失败 ${counts.failed} 项，重复 ${counts.duplicate} 项${cleanupWarnings ? `，本地文件清理警告 ${cleanupWarnings} 项` : ''}。`,
      counts.failed || counts.duplicate || cleanupWarnings ? 'error' : 'info',
    )
  }

  async function submitPushBatch() {
    if (pushBatch.saving) return
    pushBatch.error = ''
    if (!dispatchForm.machineIp) {
      pushBatch.error = '请选择目标机床。'
      return
    }
    if (!pushBatch.items.length) {
      pushBatch.error = '请至少保留一个文件。'
      return
    }
    if (invalidBatchCount.value) {
      pushBatch.error = `还有 ${invalidBatchCount.value} 个文件的加工参数不完整，请先补充。`
      return
    }
    if (nonStandardBatchCount.value && !pushBatch.nonStandardConfirmed) {
      pushBatch.error = `请先确认已人工核对 ${nonStandardBatchCount.value} 个非标准文件名及其加工参数。`
      return
    }
    if (overrideBatchCount.value && !pushBatch.overrideConfirmed) {
      pushBatch.error = `有 ${overrideBatchCount.value} 个文件的参数与标准文件名不一致，请先人工核对并确认。`
      return
    }
    if (!systemStatus.value?.can_push) {
      pushBatch.error = '当前文件来源或推送服务不可用。'
      return
    }

    pushBatch.saving = true
    pushBatch.resultsReady = false
    pushBatch.completed = 0
    pushBatch.total = pushBatch.items.length
    pushBatch.currentFilename = '正在创建独立派发任务'
    pushBatch.items.forEach((item) => {
      item.status = 'pending'
      item.error = ''
      item.dispatchId = ''
      item.retryable = false
      item.statusChecked = false
    })

    try {
      const created = await createCuttingDispatchBatch({
        machine_ip: dispatchForm.machineIp,
        items: pushBatch.items.map(batchRequestItem),
      })
      const createdItems = []
      for (const [index, item] of pushBatch.items.entries()) {
        const result = created.items?.[index]
        if (result && String(result.document_id) !== item.documentId) {
          item.status = 'failed'
          item.error = '任务创建结果与所选文件不一致，请核对派发记录'
          continue
        }
        if (applyCreationResult(item, result)) createdItems.push(item)
      }

      pushBatch.completed = pushBatch.items.length - createdItems.length
      for (const item of createdItems) {
        await executeBatchItem(item)
        pushBatch.completed += 1
      }

      pushBatch.resultsReady = true
      pushBatch.currentFilename = ''
      clearSelectedFiles()
      await refreshBatchHistory()
    } catch (caught) {
      const error = toError(caught)
      pushBatch.error = `任务创建结果未确认：${error.message}。重新提交时已创建的活动任务会显示为重复，请先核对其状态。`
    } finally {
      pushBatch.saving = false
      operatingId.value = ''
    }
  }

  async function retryBatchItem(item: PushBatchItem) {
    if (pushBatch.saving || !['failed', 'duplicate'].includes(item.status) || isStaleFileError(item)) return
    pushBatch.saving = true
    pushBatch.error = ''
    pushBatch.completed = 0
    pushBatch.total = 1
    pushBatch.currentFilename = item.filename
    try {
      if (item.dispatchId) {
        const current = await syncBatchItem(item)
        if (current.status === 'sent' || !item.retryable) return
      } else {
        const created = await createCuttingDispatchBatch({
          machine_ip: dispatchForm.machineIp,
          items: [batchRequestItem(item)],
        })
        if (!applyCreationResult(item, created.items?.[0])) return
      }
      await executeBatchItem(item)
    } catch (caught) {
      const error = toError(caught)
      item.status = 'failed'
      item.retryable = false
      item.error = `${error.message}；请核对任务状态后再重试`
    } finally {
      pushBatch.completed = 1
      pushBatch.currentFilename = ''
      pushBatch.saving = false
      await refreshBatchHistory()
    }
  }

  async function checkBatchItem(item: PushBatchItem) {
    if (pushBatch.saving || !item.dispatchId) return
    pushBatch.saving = true
    try {
      await syncBatchItem(item)
    } catch (caught) {
      const error = toError(caught)
      item.retryable = false
      item.error = `任务状态核对失败：${error.message}`
    } finally {
      pushBatch.saving = false
      await Promise.allSettled([loadHistory(), refreshVisibleFileStatuses()])
    }
  }

  async function refreshBatchCatalog() {
    if (pushBatch.saving) return
    closePushBatch()
    clearSelectedFiles()
    await loadFiles(1)
  }

  return { bulkQuantity, pushBatch, pushBatchOpen, invalidBatchCount, nonStandardBatchCount, overrideBatchCount, batchStatusCounts, batchItemError, hasNameMismatch, openPushBatch, closePushBatch, removePushBatchItem, applyBulkQuantity, invalidateBatchConfirmation, batchStatusLabel, isStaleFileError, submitPushBatch, retryBatchItem, checkBatchItem, refreshBatchCatalog }
}
