import type { Ref } from 'vue'
import type { AutomationExecution, AutomationSchedule, SchedulePayload } from '../types/automation'
import { computed, onActivated, onBeforeUnmount, onDeactivated, reactive, ref, watch } from 'vue'
import { deleteAutomationSchedule, getAutomationRun, runAutomationSchedule, saveAutomationSchedule } from '../api/automation'
import { deviceSelection } from '../state/devices'
import { toError } from '../utils/errors'

export function useAutomationSchedules({ hasUnsavedChanges, message, error, refresh, scrollToEditor, confirm }: {
  hasUnsavedChanges: Ref<boolean>
  message: Ref<string>
  error: Ref<string>
  refresh: () => Promise<void>
  scrollToEditor: () => void
  confirm: (message: string) => Promise<boolean>
}) {
  const saving = ref(false)
  const editingId = ref('')
  const executions = reactive<Record<string, AutomationExecution>>({})
  const runningScheduleIds = computed(() => Object.values(executions).filter(isRunning).map(item => item.schedule_id))
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const controllers = new Map<string, AbortController>()
  let active = true
  let disposed = false

  function isRunning(execution: AutomationExecution) {
    return !['sent', 'failed', 'unknown', 'submission_failed'].includes(execution.status)
  }

  function schedulePoll(execution: AutomationExecution) {
    if (!active || !execution.run_id || !isRunning(execution)) return
    const id = execution.schedule_id
    clearTimeout(timers.get(id))
    timers.set(id, setTimeout(() => { timers.delete(id); void pollExecution(execution) }, 2000))
  }

  async function pollExecution(execution: AutomationExecution) {
    if (!active || !execution.run_id) return
    const id = execution.schedule_id
    const controller = new AbortController()
    controllers.set(id, controller)
    try {
      const run = await getAutomationRun(execution.run_id, { signal: controller.signal })
      if (!active || controller.signal.aborted || executions[id] !== execution) return
      const changed = execution.status !== run.status
      Object.assign(execution, { status: run.status, last_error: run.last_error, monitoring_error: '' })
      if (changed) await refresh()
    } catch (caught) {
      if (active && !controller.signal.aborted && executions[id] === execution) {
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
    Object.values(executions).forEach(schedulePoll)
  })
  onBeforeUnmount(() => { disposed = true; pauseExecutions() })
  const emptyForm = (): Omit<Required<SchedulePayload>, 'recipients' | 'weekday'> & { recipients_text: string; weekday: number } => ({
    device_id: deviceSelection.id,
    name: '每日车间报表',
    report_type: 'workshop',
    frequency: 'daily',
    run_time: '18:00',
    weekday: 1,
    month_day: 1,
    report_period: 'previous_day',
    recipients_text: '',
    subject_template: '{report_name} - {report_date}',
    chart_type: 'bar_line',
    attach_report: true,
    max_attempts: 3,
    enabled: true,
  })
  const form = reactive(emptyForm())

  function changeReportType() {
    const periods: NonNullable<SchedulePayload['report_period']>[] = form.report_type === 'leadership' ? ['previous_month', 'current_month'] : ['previous_day', 'current_day']
    if (!periods.includes(form.report_period)) form.report_period = periods[0]
    hasUnsavedChanges.value = true
  }

  function resetForm() {
    editingId.value = ''
    Object.assign(form, emptyForm())
    hasUnsavedChanges.value = false
  }

  function editSchedule(item: AutomationSchedule) {
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
      recipients_text: item.recipients.join('\n'),
      subject_template: item.subject_template,
      chart_type: item.chart_type,
      attach_report: item.attach_report,
      max_attempts: item.max_attempts,
      enabled: item.enabled,
    })
    scrollToEditor()
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
      recipients: form.recipients_text.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean),
      subject_template: form.subject_template,
      chart_type: form.chart_type,
      attach_report: form.attach_report,
      max_attempts: Number(form.max_attempts),
      enabled: form.enabled,
    }
  }

  async function submit() {
    saving.value = true
    error.value = ''
    message.value = ''
    try {
      await saveAutomationSchedule(payload(), editingId.value)
      message.value = editingId.value ? '定时任务已更新' : '定时任务已创建'
      resetForm()
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message
    } finally {
      saving.value = false
    }
  }

  async function runNow(item: AutomationSchedule) {
    if (disposed || runningScheduleIds.value.includes(item.schedule_id)) return
    const execution = reactive<AutomationExecution>({
      schedule_id: item.schedule_id, schedule_name: item.name, status: 'submitting',
    })
    executions[item.schedule_id] = execution
    error.value = ''
    message.value = ''
    try {
      const run = await runAutomationSchedule(item.schedule_id)
      if (disposed) return
      Object.assign(execution, { run_id: run.run_id, status: run.status, last_error: run.last_error })
      schedulePoll(execution)
      message.value = `已提交“${item.name}”，报表生成后将自动发送。`
      await refresh()
    } catch (caught) {
      if (!disposed) {
        execution.status = 'submission_failed'
        execution.last_error = `提交未确认：${toError(caught).message}。请先查看发送记录，避免重复执行。`
      }
    }
  }

  async function remove(item: AutomationSchedule) {
    if (!await confirm(`确定删除定时任务“${item.name}”？历史发送记录会保留。`)) return
    try {
      await deleteAutomationSchedule(item.schedule_id)
      if (editingId.value === item.schedule_id) resetForm()
      message.value = '定时任务已删除'
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message
    }
  }

  watch(() => deviceSelection.id, (id) => { if (!form.device_id) form.device_id = id })

  return { saving, editingId, form, executions, runningScheduleIds, changeReportType, resetForm, editSchedule, submit, runNow, remove }
}
