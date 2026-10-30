import type { Ref } from 'vue'
import type { User } from '../types/auth'
import type { ConfirmationAudit, WorkshopTask } from '../types/reports'
import { computed, reactive, watch } from 'vue'
import { correctWorkshopTask, listWorkshopConfirmationAudits, revokeWorkshopTask } from '../api/cutting'
import { listUserAccounts } from '../api/auth'
import { toError } from '../utils/errors'

export function useWorkshopCorrection({ isWorker, user, filters, rowDate, showConfirmationFeedback, refresh }: {
  isWorker: Ref<boolean>
  user: () => User
  filters: { reportDate: string }
  rowDate: (row: WorkshopTask) => string
  showConfirmationFeedback: (type: string, title: string, message: string, timeout?: number) => void
  refresh: () => Promise<void>
}) {
  const correction = reactive({
    visible: false,
    row: null as WorkshopTask | null,
    action: 'revoke' as 'revoke' | 'reassign',
    workerId: '',
    reason: '',
    saving: false,
    loading: false,
    error: '',
    workers: [] as User[],
    audits: [] as ConfirmationAudit[],
  })
  const correctionDialogOpen = computed(() => correction.visible)
  const correctionReasonPresets = {
    workerRevoke: [
      '误选了其他工人的任务',
      '任务并非本人完成',
      '任务信息有误，需重新核对',
    ],
    adminRevoke: [
      '原确认人选择错误',
      '暂时无法确认实际完成人',
      '任务记录异常，待现场核查',
    ],
    adminReassign: [
      '根据现场记录更正实际完成人',
      '班组核对后调整任务归属',
      '原确认人误选，改派给正确人员',
    ],
  }
  const presetReasonValues = new Set(Object.values(correctionReasonPresets).flat())
  const visibleCorrectionReasonPresets = computed(() => {
    if (isWorker.value) return correctionReasonPresets.workerRevoke
    return correction.action === 'reassign'
      ? correctionReasonPresets.adminReassign
      : correctionReasonPresets.adminRevoke
  })
  const availableWorkers = computed(() => correction.workers.filter((worker) => (
    worker.role === 'worker' && worker.active && worker.user_id !== correction.row?.confirmed_by_id
  )))

  function isOwnConfirmation(row: WorkshopTask) {
    return isWorker.value && row.confirmed_by_id === user().user_id
  }

  async function openCorrection(row: WorkshopTask) {
    if (!row.confirmed_by || (isWorker.value && !isOwnConfirmation(row))) return
    Object.assign(correction, {
      visible: true,
      row,
      action: 'revoke',
      workerId: '',
      reason: '',
      saving: false,
      loading: !isWorker.value,
      error: '',
      workers: [] as User[],
      audits: [] as ConfirmationAudit[],
    })
    if (isWorker.value) return
    try {
      const [users, auditResult] = await Promise.all([
        listUserAccounts(),
        listWorkshopConfirmationAudits({ reportDate: rowDate(row), taskId: row.task_id }),
      ])
      correction.workers = users.items || []
      correction.audits = auditResult.items || []
    } catch (caught) {
      const exception = toError(caught)
      correction.error = exception.message
    } finally {
      correction.loading = false
    }
  }

  function closeCorrection() {
    if (correction.saving) return
    correction.visible = false
    correction.row = null
  }

  function selectCorrectionReason(reason: string) {
    correction.reason = reason
    correction.error = ''
  }

  async function submitCorrection() {
    const reason = correction.reason.trim()
    if (reason.length < 2) {
      correction.error = '请填写至少 2 个字的原因'
      return
    }
    if (!isWorker.value && correction.action === 'reassign' && !correction.workerId) {
      correction.error = '请选择改派后的工人'
      return
    }
    correction.saving = true
    correction.error = ''
    const row = correction.row
    if (!row) return
    try {
      if (isWorker.value) {
        await revokeWorkshopTask(row.task_id, filters.reportDate, reason)
        showConfirmationFeedback('success', '已撤销确认', `${row.program_no || '--'} 已恢复为待确认`, 5000)
      } else {
        await correctWorkshopTask(row.task_id, {
          reportDate: rowDate(row),
          action: correction.action,
          workerId: correction.action === 'reassign' ? correction.workerId : null,
          reason,
        })
        const target = correction.workers.find((worker) => worker.user_id === correction.workerId)
        const message = correction.action === 'reassign'
          ? `${row.program_no || '--'} 已改派给 ${target?.display_name || '所选工人'}`
          : `${row.program_no || '--'} 已恢复为待确认`
        showConfirmationFeedback('success', '更正已保存', message, 5000)
      }
      correction.visible = false
      correction.row = null
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      correction.error = exception.message
      showConfirmationFeedback('error', '更正失败', exception.message, 7000)
    } finally {
      correction.saving = false
    }
  }

  watch(() => correction.action, () => {
    if (!isWorker.value && presetReasonValues.has(correction.reason)) correction.reason = ''
  })

  return { correction, correctionDialogOpen, visibleCorrectionReasonPresets, availableWorkers, isOwnConfirmation, openCorrection, closeCorrection, selectCorrectionReason, submitCorrection }
}

export type WorkshopCorrectionState = ReturnType<typeof useWorkshopCorrection>['correction']
