import { reportAsyncError } from '../api/browserObservations.ts'
import type { Ref } from 'vue'
import type { ConfirmOptions } from './useConfirm'
import type { AutomationExecution, AutomationSchedule, SchedulePayload } from '../types/automation'
import { computed, onActivated, onBeforeUnmount, onDeactivated, reactive, ref, watch } from 'vue'
import { cancelAutomationRun, deleteAutomationSchedule, getAutomationRun, runAutomationSchedule, saveAutomationSchedule } from '../api/automation'
import { deviceSelection } from '../state/devices'
import { toError } from '../utils/errors'

export function useAutomationSchedules({ hasUnsavedChanges, message, error, refresh, confirm }: {
  hasUnsavedChanges: Ref<boolean>
  message: Ref<string>
  error: Ref<string>
  refresh: () => Promise<void>
  confirm: (options: ConfirmOptions) => Promise<boolean>
}) {
  const saving = ref(false)
  const editingId = ref('')
  const scheduleError = ref('')
  const executions = reactive<Partial<Record<string, AutomationExecution>>>({})
  const activeExecutions = () => Object.values(executions).filter(item => item !== undefined)
  const runningScheduleIds = computed(() => activeExecutions().filter(isRunning).map(item => item.schedule_id))
  const cancellableScheduleIds = computed(() => activeExecutions().filter(canCancel).map(item => item.schedule_id))
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const removing = new Set<string>()
  const controllers = new Map<string, AbortController>()
  let active = true
  const isActive = () => active
  let disposed = false
  const isDisposed = () => disposed

  function isRunning(execution: AutomationExecution) {
    return !['sent', 'failed', 'unknown', 'cancelled', 'submission_failed'].includes(execution.status)
  }

  function canCancel(execution: AutomationExecution) {
    return !!execution.run_id && ['queued', 'generating', 'analysis_wait', 'email_pending', 'retry_wait'].includes(execution.status)
  }

  function schedulePoll(execution: AutomationExecution) {
    if (!isActive() || !execution.run_id || !isRunning(execution) || execution.cancelling) return
    const id = execution.schedule_id
    clearTimeout(timers.get(id))
    timers.set(id, setTimeout(() => { timers.delete(id); pollExecution(execution).catch(reportAsyncError) }, 2000))
  }

  async function pollExecution(execution: AutomationExecution) {
    if (!isActive() || !execution.run_id) return
    const id = execution.schedule_id
    const controller = new AbortController()
    controllers.set(id, controller)
    try {
      const run = await getAutomationRun(execution.run_id, { signal: controller.signal })
      if (!isActive() || controller.signal.aborted || executions[id] !== execution) return
      const changed = execution.status !== run.status
      Object.assign(execution, { status: run.status, last_error: run.last_error, monitoring_error: '' })
      if (changed) await refresh()
    } catch (caught) {
      if (isActive() && !controller.signal.aborted && executions[id] === execution) {
        execution.monitoring_error = `进度暂时读取失败：${toError(caught).message}。正在重新读取，请勿重复执行。`
      }
    } finally {
      if (controllers.get(id) === controller) {
        controllers.delete(id)
        schedulePoll(execution)
      }
    }
  }

  function pauseExecutions() {
    active = false
    timers.forEach(timer => clearTimeout(timer))
    timers.clear()
    controllers.forEach(controller => controller.abort())
    controllers.clear()
  }
  onDeactivated(pauseExecutions)
  onActivated(() => {
    active = true
    activeExecutions().forEach(schedulePoll)
  })
  onBeforeUnmount(() => { disposed = true; pauseExecutions() })
  const recipientInputVisible = ref(false)
  const recipientDraft = ref('')
  const recipientError = ref('')
  const emptyForm = (): Omit<Required<SchedulePayload>, 'weekday'> & { weekday: number } => ({
    device_id: deviceSelection.items.find(line => line.type === 'cutting')?.id || '',
    name: '每日车间报表',
    report_type: 'workshop',
    frequency: 'daily',
    run_time: '18:00',
    weekday: 1,
    month_day: 1,
    report_period: 'previous_day',
    recipients: [],
    subject_template: '{report_name} - {report_date}',
    chart_type: 'bar_line',
    attach_report: true,
    max_attempts: 3,
    enabled: true,
  })
  const form = reactive(emptyForm())

  function cancelRecipientInput() {
    recipientInputVisible.value = false
    recipientDraft.value = ''
    recipientError.value = ''
  }

  function addRecipient() {
    const addresses = recipientDraft.value.split(/[\n,;，；]+/).map(item => item.trim().toLowerCase()).filter(Boolean)
    if (!addresses.length || addresses.some(address => address.length > 320 || !/^[^\s@,;，；]+@[^\s@,;，；]+$/.test(address))) {
      recipientError.value = '请输入有效的邮箱地址'
      return false
    }
    const recipients = [...new Set([...form.recipients, ...addresses])]
    if (recipients.length > 20) {
      recipientError.value = '最多添加 20 个收件人'
      return false
    }
    form.recipients = recipients
    hasUnsavedChanges.value = true
    cancelRecipientInput()
    return true
  }

  function removeRecipient(address: string) {
    form.recipients = form.recipients.filter(item => item !== address)
    hasUnsavedChanges.value = true
    recipientError.value = ''
  }

  function changeReportType() {
    const periods: NonNullable<SchedulePayload['report_period']>[] = form.report_type === 'leadership' ? ['previous_month', 'current_month'] : ['previous_day', 'current_day']
    if (!periods.includes(form.report_period)) form.report_period = periods[0]
    hasUnsavedChanges.value = true
  }

  function resetForm() {
    cancelRecipientInput()
    scheduleError.value = ''
    editingId.value = ''
    Object.assign(form, emptyForm())
    hasUnsavedChanges.value = false
  }

  function editSchedule(item: AutomationSchedule) {
    cancelRecipientInput()
    scheduleError.value = ''
    hasUnsavedChanges.value = false
    editingId.value = item.schedule_id
    Object.assign(form, {
      device_id: item.device_id,
      name: item.name,
      report_type: item.report_type,
      frequency: item.frequency,
      run_time: item.run_time,
      weekday: item.weekday || 1,
      month_day: item.month_day || 1,
      report_period: item.report_period,
      recipients: [...item.recipients],
      subject_template: item.subject_template,
      chart_type: item.chart_type,
      attach_report: item.attach_report,
      max_attempts: item.max_attempts,
      enabled: item.enabled,
    })
  }

  function payload() {
    return {
      device_id: form.device_id,
      name: form.name,
      report_type: form.report_type,
      frequency: form.frequency,
      run_time: form.run_time,
      weekday: form.frequency === 'weekly' ? Number(form.weekday) : null,
      month_day: form.frequency === 'monthly' ? Number(form.month_day) : null,
      report_period: form.report_period,
      recipients: [...form.recipients],
      subject_template: form.subject_template,
      chart_type: form.chart_type,
      attach_report: form.attach_report,
      max_attempts: Number(form.max_attempts),
      enabled: form.enabled,
    }
  }

  async function submit() {
    if (saving.value) return false
    scheduleError.value = ''
    if (recipientDraft.value.trim() && !addRecipient()) return
    if (!form.recipients.length) {
      recipientInputVisible.value = true
      recipientError.value = '至少添加一个收件人'
      return
    }
    saving.value = true
    error.value = ''
    message.value = ''
    try {
      await saveAutomationSchedule(payload(), editingId.value)
      if (isDisposed()) return false
      message.value = editingId.value ? '定时任务已更新' : '定时任务已创建'
      resetForm()
      await refresh()
      return true
    } catch (caught) {
      if (!isDisposed()) scheduleError.value = toError(caught).message
      return false
    } finally {
      saving.value = false
    }
  }

  async function runNow(item: AutomationSchedule) {
    if (isDisposed() || runningScheduleIds.value.includes(item.schedule_id)) return
    const execution = reactive<AutomationExecution>({
      schedule_id: item.schedule_id, schedule_name: item.name, status: 'submitting',
    })
    executions[item.schedule_id] = execution
    error.value = ''
    message.value = ''
    try {
      const run = await runAutomationSchedule(item.schedule_id)
      if (isDisposed()) return
      Object.assign(execution, { run_id: run.run_id, status: run.status, last_error: run.last_error })
      schedulePoll(execution)
      message.value = `已提交“${item.name}”，报表生成后将自动发送。`
      await refresh()
    } catch (caught) {
      if (!isDisposed()) {
        execution.status = 'submission_failed'
        execution.last_error = `提交未确认：${toError(caught).message}。请先查看发送记录，避免重复执行。`
      }
    }
  }

  function restoreExecutions(items: AutomationSchedule[]) {
    for (const item of items) {
      const id = item.active_manual_run_id
      const status = item.active_manual_run_status
      const current = executions[item.schedule_id]
      if (!id || !status || (current && (current.run_id === id || isRunning(current)))) continue
      const execution = reactive<AutomationExecution>({ run_id: id, schedule_id: item.schedule_id, schedule_name: item.name, status })
      executions[item.schedule_id] = execution
      schedulePoll(execution)
    }
  }

  async function cancelExecution(execution: AutomationExecution) {
    const id = execution.schedule_id
    const runId = execution.run_id
    if (!runId || !canCancel(execution) || execution.cancelling || isDisposed()) return
    execution.cancelling = true
    clearTimeout(timers.get(id))
    timers.delete(id)
    controllers.get(id)?.abort()
    controllers.delete(id)
    error.value = ''
    message.value = ''
    try {
      const result = await cancelAutomationRun(runId)
      if (isDisposed()) return
      Object.assign(execution, { status: result.status, last_error: result.last_error, monitoring_error: '' })
      message.value = `已取消“${execution.schedule_name}”的本次发送。`
      await refresh()
    } catch (caught) {
      if (!isDisposed()) error.value = toError(caught).message
    } finally {
      execution.cancelling = false
      schedulePoll(execution)
    }
  }

  async function remove(item: AutomationSchedule) {
    const id = item.schedule_id
    const isSaving = () => saving.value
    if (isSaving() || isDisposed() || removing.has(id)) return
    removing.add(id)
    try {
      if (!await confirm({
        title: '删除定时任务？', content: `“${item.name}”将不再按计划运行。历史发送记录会保留。`,
        positiveText: '删除定时任务', type: 'error',
      }) || isSaving() || isDisposed()) return
      await deleteAutomationSchedule(id)
      if (isDisposed()) return
      if (editingId.value === id) resetForm()
      message.value = '定时任务已删除'
      await refresh()
    } catch (caught) {
      if (!isDisposed()) error.value = toError(caught).message
    } finally {
      removing.delete(id)
    }
  }

  watch(() => deviceSelection.items, () => { if (!form.device_id) form.device_id = emptyForm().device_id })

  return { saving, editingId, form, executions, runningScheduleIds, cancellableScheduleIds, changeReportType, resetForm, editSchedule, submit, runNow, remove, scheduleError,
    restoreExecutions, cancelExecution,
    recipientInputVisible, recipientDraft, recipientError, addRecipient, removeRecipient, cancelRecipientInput }
}
