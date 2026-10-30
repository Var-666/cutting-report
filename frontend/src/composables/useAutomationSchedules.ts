import type { Ref } from 'vue'
import type { AutomationSchedule } from '../types/automation'
import { reactive, ref, watch } from 'vue'
import { deleteAutomationSchedule, runAutomationSchedule, saveAutomationSchedule } from '../api/automation'
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
  const emptyForm = () => ({
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
    const periods = form.report_type === 'leadership' ? ['previous_month', 'current_month'] : ['previous_day', 'current_day']
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
    error.value = ''
    message.value = ''
    try {
      const run = await runAutomationSchedule(item.schedule_id)
      if (run.status === 'failed') throw new Error(run.last_error || '报表任务创建失败')
      message.value = `已提交“${item.name}”，报表生成后将自动发送。`
      await refresh()
    } catch (caught) {
      const exception = toError(caught)
      error.value = exception.message
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

  return { saving, editingId, form, changeReportType, resetForm, editSchedule, submit, runNow, remove }
}
