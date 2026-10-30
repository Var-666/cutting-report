import type { Ref } from 'vue'
import type { User } from '../types/auth'
import type { TaskConfirmation, WorkshopResult, WorkshopTask } from '../types/reports'
import { computed, reactive, ref } from 'vue'
import { confirmWorkshopTasks } from '../api/cutting'
import { toError } from '../utils/errors'

export function useWorkshopConfirmation({ isWorker, user, result, filters, error, load }: {
  isWorker: Ref<boolean>
  user: () => User
  result: Ref<WorkshopResult>
  filters: { reportDate: string }
  error: Ref<string>
  load: (page: number, options: { silent: boolean; ensureReport: boolean }) => Promise<void>
}) {
  const selectedTasks = ref(new Map<string, WorkshopTask>())
  const batchConfirmation = reactive({ visible: false, saving: false, error: '', tasks: [] as WorkshopTask[] })
  const confirmationFeedback = reactive({ visible: false, type: 'pending', title: '', message: '' })
  const batchDialogOpen = computed(() => batchConfirmation.visible)
  const selectableTasks = computed(() => isWorker.value ? result.value.items.filter((row) => !row.confirmed_by) : [])
  const selectedTaskCount = computed(() => selectedTasks.value.size)
  const allVisibleTasksSelected = computed(() => (
    selectableTasks.value.length > 0
    && selectableTasks.value.every((row) => selectedTasks.value.has(row.task_id))
  ))
  const someVisibleTasksSelected = computed(() => (
    !allVisibleTasksSelected.value
    && selectableTasks.value.some((row) => selectedTasks.value.has(row.task_id))
  ))
  let feedbackTimer: number | null = null

  function clearSelectedTasks() {
    selectedTasks.value = new Map()
  }

  function setTaskSelected(row: WorkshopTask, selected: boolean) {
    if (!isWorker.value || row.confirmed_by || batchConfirmation.saving) return
    const next = new Map(selectedTasks.value)
    if (selected) next.set(row.task_id, row)
    else next.delete(row.task_id)
    selectedTasks.value = next
  }

  function toggleVisibleTasks(selected: boolean) {
    const next = new Map(selectedTasks.value)
    for (const row of selectableTasks.value) {
      if (selected) next.set(row.task_id, row)
      else next.delete(row.task_id)
    }
    selectedTasks.value = next
  }

  function reconcileVisibleTaskSelection(items: WorkshopTask[]) {
    if (!selectedTasks.value.size) return
    const next = new Map(selectedTasks.value)
    for (const row of items) {
      if (!next.has(row.task_id)) continue
      if (row.confirmed_by) next.delete(row.task_id)
      else next.set(row.task_id, row)
    }
    selectedTasks.value = next
  }

  function openBatchConfirmation(tasks = [...selectedTasks.value.values()]) {
    const pendingTasks = tasks.filter((row) => !row.confirmed_by)
    if (!isWorker.value || !pendingTasks.length || batchConfirmation.saving) return
    batchConfirmation.tasks = pendingTasks
    batchConfirmation.error = ''
    batchConfirmation.visible = true
  }

  function openSingleConfirmation(row: WorkshopTask) {
    if (row.confirmed_by) return
    selectedTasks.value = new Map([[row.task_id, row]])
    openBatchConfirmation([row])
  }

  function closeBatchConfirmation() {
    if (batchConfirmation.saving) return
    batchConfirmation.visible = false
    batchConfirmation.error = ''
  }

  function showConfirmationFeedback(type: string, title: string, message: string, timeout = 0) {
    if (feedbackTimer) window.clearTimeout(feedbackTimer)
    Object.assign(confirmationFeedback, { visible: true, type, title, message })
    if (timeout) {
      feedbackTimer = window.setTimeout(() => {
        confirmationFeedback.visible = false
        feedbackTimer = null
      }, timeout)
    }
  }

  function closeConfirmationFeedback() {
    if (feedbackTimer) window.clearTimeout(feedbackTimer)
    feedbackTimer = null
    confirmationFeedback.visible = false
  }

  function applyConfirmedTasks(confirmations: TaskConfirmation[] = []) {
    const confirmationByTaskId = new Map(
      confirmations.map((confirmation) => [String(confirmation.task_id), confirmation]),
    )
    if (!confirmationByTaskId.size) return

    let newlyConfirmed = 0
    const items = result.value.items.map((row) => {
      const confirmation = confirmationByTaskId.get(String(row.task_id))
      if (!confirmation) return row
      if (!row.confirmed_by) newlyConfirmed += 1
      return {
        ...row,
        confirmed_by_id: confirmation.worker_id,
        confirmed_by: confirmation.worker_name,
        confirmed_by_company_no: confirmation.worker_company_no ?? row.confirmed_by_company_no ?? null,
        confirmed_at: confirmation.confirmed_at,
      }
    })
    result.value = {
      ...result.value,
      items,
      confirmed_count: Number(result.value.confirmed_count || 0) + newlyConfirmed,
    }
    reconcileVisibleTaskSelection(items)
  }

  async function submitBatchConfirmation() {
    if (!isWorker.value || batchConfirmation.saving || !batchConfirmation.tasks.length) return
    const tasks = [...batchConfirmation.tasks]
    batchConfirmation.saving = true
    batchConfirmation.error = ''
    error.value = ''
    showConfirmationFeedback('pending', '正在确认任务', `共 ${tasks.length} 项 · 请稍候`)
    try {
      const confirmationResult = await confirmWorkshopTasks(tasks.map((row) => row.task_id), filters.reportDate,
        Object.fromEntries(tasks.map((row) => [row.task_id, row.snapshot_revision || 1])))
      const confirmations = confirmationResult.items || []
      applyConfirmedTasks(confirmations)
      batchConfirmation.visible = false
      batchConfirmation.tasks = []
      clearSelectedTasks()
      showConfirmationFeedback(
        'success',
        '确认成功',
        `${tasks.length} 项任务已登记为由 ${user().display_name} 完成`,
        5000,
      )
      await load(result.value.page, { silent: true, ensureReport: false })
      // 即使后台刷新短暂返回旧快照，也不能让已确认任务重新显示待确认按钮。
      applyConfirmedTasks(confirmations)
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message
      batchConfirmation.error = exception.message
      showConfirmationFeedback('error', '确认失败', exception.message, 7000)
    } finally {
      batchConfirmation.saving = false
    }
  }

  function pauseFeedback() {
    if (feedbackTimer) window.clearTimeout(feedbackTimer)
    feedbackTimer = null
  }

  return { selectableTasks, selectedTasks, batchConfirmation, confirmationFeedback, batchDialogOpen, selectedTaskCount, allVisibleTasksSelected, someVisibleTasksSelected, clearSelectedTasks, setTaskSelected, toggleVisibleTasks, reconcileVisibleTaskSelection, openBatchConfirmation, openSingleConfirmation, closeBatchConfirmation, showConfirmationFeedback, closeConfirmationFeedback, submitBatchConfirmation, pauseFeedback }
}

export type WorkshopBatchConfirmation = ReturnType<typeof useWorkshopConfirmation>['batchConfirmation']
