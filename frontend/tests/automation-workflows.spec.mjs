// @vitest-environment jsdom

import assert from 'node:assert/strict'
import { enableAutoUnmount, flushPromises as flushAutomation, mount } from '@vue/test-utils'
import { afterEach, onTestFinished, test, vi } from 'vitest'
import { h, KeepAlive, onBeforeUnmount, ref, effectScope } from 'vue'
import * as api from '../src/api/automation'
import { useAutomationConfiguration } from '../src/composables/useAutomationConfiguration'
import { useAutomationDeliveries } from '../src/composables/useAutomationDeliveries'
import { useAutomationSchedules } from '../src/composables/useAutomationSchedules'

enableAutoUnmount(afterEach)
afterEach(() => { vi.useRealTimers() })

async function mountAutomation({ confirm = async () => true, run, readRun, save, scheduleItems = [] } = {}) {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  const calls = { emails: [], actions: [], runReads: [], savedSchedules: [] }
  let held = false
  const result = (page, total = 60) => ({ items: total ? [{ delivery_id: `page-${page}` }] : [], total, page, page_size: 10 })
  vi.spyOn(api, 'listEmailDeliveries').mockImplementation((query, options) => {
    if (held) return new Promise((resolve, reject) => calls.emails.push({
      ...query, signal: options.signal, resolve: (total = 60) => resolve(result(query.page, total)), reject,
    }))
    calls.emails.push({ ...query, signal: options.signal })
    return Promise.resolve(result(query.page))
  })
  vi.spyOn(api, 'saveAutomationSchedule').mockImplementation(async (body, id) => {
    calls.actions.push('save'); calls.savedSchedules.push({ body, id }); await save?.()
  })
  vi.spyOn(api, 'runAutomationSchedule').mockImplementation(async id => {
    calls.actions.push('run')
    return run ? run(id) : { schedule_id: id, run_id: `run-${id}`, status: 'queued' }
  })
  vi.spyOn(api, 'cancelAutomationRun').mockImplementation(async id => {
    calls.actions.push(`cancel:${id}`)
    return { run_id: id, status: 'cancelled' }
  })
  vi.spyOn(api, 'getAutomationRun').mockImplementation(async (id, options) => {
    calls.runReads.push({ id, signal: options.signal })
    return readRun ? readRun(id, options) : { run_id: id, status: 'sent' }
  })
  const shown = ref(true)
  let state
  const page = { setup() {
    const hasUnsavedChanges = ref(false)
    const message = ref('')
    const error = ref('')
    let disposed = false
    const refresh = async () => { await delivery.loadDeliveries() }
    const schedules = useAutomationSchedules({ message, error, hasUnsavedChanges, refresh, confirm })
    const delivery = useAutomationDeliveries({ message, error, refresh, isDisposed: () => disposed, confirm })
    onBeforeUnmount(() => { disposed = true; delivery.pauseDeliveries() })
    state = { ...schedules, ...delivery, error, hasUnsavedChanges }
    schedules.restoreExecutions(scheduleItems)
    return () => null
  } }
  const wrapper = mount({
    render: () => h(KeepAlive, {}, { default: () => shown.value ? h(page) : null }),
  })
  await state.loadDeliveries()
  return { state, calls, unmount: () => wrapper.unmount(), hold() { held = true },
    async pause() { shown.value = false; await flushAutomation() },
    async resume() { shown.value = true; await flushAutomation() },
    poll: () => vi.advanceTimersByTimeAsync(2000),
  }
}

test('run now immediately shows submission, blocks duplicate clicks and follows this run until sent', async () => {
  let submit
  let stage = 'generating'
  const { state, calls, poll } = await mountAutomation({
    run: () => new Promise(resolve => { submit = resolve }),
    readRun: async id => ({ run_id: id, status: stage }),
  })
  const item = { schedule_id: 'saved', name: '日报' }
  const pending = state.runNow(item)
  assert.equal(state.executions.saved.status, 'submitting')
  assert.deepEqual(state.runningScheduleIds.value, ['saved'])
  await state.runNow(item)
  assert.deepEqual(calls.actions, ['run'])
  submit({ run_id: 'exact-run', status: 'queued' })
  await pending
  for (const status of ['generating', 'email_pending', 'sending', 'retry_wait', 'sent']) {
    stage = status
    await poll()
    assert.equal(state.executions.saved.status, status)
    assert.equal(calls.runReads.at(-1).id, 'exact-run')
  }
  assert.deepEqual(state.runningScheduleIds.value, [])
  assert.equal(vi.getTimerCount(), 0)
})

test('run progress keeps concurrent schedules isolated and stops on failure or unknown result', async () => {
  const { state, poll } = await mountAutomation({
    readRun: async id => ({ status: id === 'run-first' ? 'failed' : 'unknown', last_error: `${id} error`, schedule_id: null }),
  })
  await state.runNow({ schedule_id: 'first', name: '日报' })
  await state.runNow({ schedule_id: 'second', name: '月报' })
  assert.deepEqual(state.runningScheduleIds.value, ['first', 'second'])
  await poll()
  assert.equal(state.executions.first.status, 'failed')
  assert.equal(state.executions.second.status, 'unknown')
  assert.equal(state.executions.first.last_error, 'run-first error')
  assert.equal(state.executions.second.schedule_id, 'second')
  assert.deepEqual(state.runningScheduleIds.value, [])
  assert.equal(vi.getTimerCount(), 0)
})

test('progress read failures keep the run active and recover without resubmitting', async () => {
  let fail = true
  const { state, calls, poll } = await mountAutomation({
    readRun: async () => { if (fail) throw new Error('offline'); return { status: 'sent' } },
  })
  await state.runNow({ schedule_id: 'saved', name: '日报' })
  await poll()
  assert.match(state.executions.saved.monitoring_error, /offline/)
  assert.deepEqual(state.runningScheduleIds.value, ['saved'])
  fail = false
  await poll()
  assert.equal(state.executions.saved.status, 'sent')
  assert.equal(state.executions.saved.monitoring_error, '')
  assert.deepEqual(calls.actions, ['run'])
})

test('submission errors are visible locally and do not start polling or retry the POST', async () => {
  const { state, calls } = await mountAutomation({ run: async () => { throw new Error('unavailable') } })
  await state.runNow({ schedule_id: 'saved', name: '日报' })
  assert.equal(state.executions.saved.status, 'submission_failed')
  assert.match(state.executions.saved.last_error, /提交未确认.*unavailable/)
  assert.deepEqual(state.runningScheduleIds.value, [])
  assert.equal(vi.getTimerCount(), 0)
  assert.deepEqual(calls.actions, ['run'])
})

test('leaving cancels progress reads, returning resumes and unmount ignores late responses', async () => {
  const reads = []
  const { state, calls, poll, pause, resume, unmount } = await mountAutomation({
    readRun: (id, options) => new Promise(resolve => reads.push({ resolve, signal: options.signal })),
  })
  await state.runNow({ schedule_id: 'saved', name: '日报' })
  await poll()
  await pause()
  assert.equal(reads[0].signal.aborted, true)
  assert.equal(vi.getTimerCount(), 0)
  await resume()
  await poll()
  reads[0].resolve({ status: 'failed' })
  await flushAutomation()
  assert.equal(state.executions.saved.status, 'queued')
  unmount()
  assert.equal(reads[1].signal.aborted, true)
  reads[1].resolve({ status: 'sent' })
  await flushAutomation()
  assert.equal(state.executions.saved.status, 'queued')
  assert.equal(vi.getTimerCount(), 0)
  assert.equal(calls.runReads.length, 2)
})

test('restored manual runs can stop sending and ignore an aborted late progress read', async () => {
  let resolveRead
  const { state, calls, poll } = await mountAutomation({
    scheduleItems: [{ schedule_id: 'saved', name: '日报', active_manual_run_id: 'manual-1', active_manual_run_status: 'generating' }],
    readRun: () => new Promise(resolve => { resolveRead = resolve }),
  })
  assert.deepEqual(state.cancellableScheduleIds.value, ['saved'])
  await poll()
  await state.cancelExecution(state.executions.saved)
  assert.deepEqual(calls.actions, ['cancel:manual-1'])
  assert.equal(calls.runReads[0].signal.aborted, true)
  resolveRead({ status: 'sending' })
  await flushAutomation()
  assert.equal(state.executions.saved.status, 'cancelled')
  assert.equal(state.runningScheduleIds.value.length, 0)
  assert.equal(vi.getTimerCount(), 0)
})

test('automation search rejects stale results and preserves the applied query on failure', async () => {
  const { state, calls, hold } = await mountAutomation()
  hold()
  state.deliverySearch.value = 'old'
  const old = state.searchDeliveries()
  state.deliverySearch.value = 'new'
  const current = state.searchDeliveries()
  assert.equal(calls.emails.at(-2).signal.aborted, true)
  calls.emails.at(-1).resolve()
  await current
  calls.emails.at(-2).resolve()
  await old
  assert.equal(state.appliedDeliverySearch.value, 'new')
  state.deliverySearch.value = 'failed'
  const failure = state.searchDeliveries()
  calls.emails.at(-1).reject(new Error('search failed'))
  await failure
  assert.equal(state.appliedDeliverySearch.value, 'new')
  assert.match(state.error.value, /search failed/)
})

test('recipient tags normalize pasted addresses, deduplicate, remove and save an unfinished entry', async () => {
  const { state, calls } = await mountAutomation()
  state.recipientInputVisible.value = true
  state.recipientDraft.value = ' ONE@example.com，two@example.com;one@example.com；three@example.com '
  assert.equal(state.addRecipient(), true)
  assert.deepEqual(state.form.recipients, ['one@example.com', 'two@example.com', 'three@example.com'])
  assert.equal(state.recipientDraft.value, '')
  assert.equal(state.recipientInputVisible.value, false)
  assert.equal(state.hasUnsavedChanges.value, true)
  state.removeRecipient('two@example.com')
  state.recipientDraft.value = 'four@example.com'
  await state.submit()
  assert.deepEqual(calls.savedSchedules[0].body.recipients, ['one@example.com', 'three@example.com', 'four@example.com'])
  assert.deepEqual(state.form.recipients, [])
})

test('empty, invalid and oversized recipient lists block saving without losing the draft', async () => {
  const { state, calls } = await mountAutomation()
  await state.submit()
  assert.match(state.recipientError.value, /至少添加一个/)
  assert.equal(state.recipientInputVisible.value, true)
  for (const value of ['not-an-email', 'one@example.com two@example.com', 'a@@example.com', 'one@example.com，invalid', `${'a'.repeat(310)}@example.com`]) {
    state.recipientDraft.value = value
    await state.submit()
    assert.match(state.recipientError.value, /有效的邮箱/)
    assert.equal(state.recipientDraft.value, value)
    assert.deepEqual(state.form.recipients, [])
  }
  state.form.recipients = Array.from({ length: 20 }, (_, i) => `user${i}@example.com`)
  state.recipientDraft.value = 'extra@example.com'
  await state.submit()
  assert.match(state.recipientError.value, /最多添加 20/)
  assert.equal(state.form.recipients.length, 20)
  assert.equal(calls.savedSchedules.length, 0)
  state.recipientDraft.value = 'USER0@example.com'
  assert.equal(state.addRecipient(), true, 'an existing address does not exceed the limit')
})

test('editing and resetting recipients clear drafts and do not mutate saved schedules', async () => {
  const { state } = await mountAutomation()
  const saved = { schedule_id: 'saved', recipients: ['one@example.com', 'two@example.com'] }
  state.recipientDraft.value = 'unfinished'
  state.editSchedule(saved)
  assert.deepEqual(state.form.recipients, saved.recipients)
  assert.equal(state.recipientDraft.value, '')
  state.removeRecipient('one@example.com')
  assert.deepEqual(saved.recipients, ['one@example.com', 'two@example.com'])
  state.recipientDraft.value = 'cancel@example.com'
  state.cancelRecipientInput()
  assert.equal(state.recipientDraft.value, '')
  assert.deepEqual(state.form.recipients, ['two@example.com'])
  state.resetForm()
  assert.deepEqual(state.form.recipients, [])
  assert.equal(state.recipientError.value, '')
})

test('email paging: latest request wins and page is committed together with records', async () => {
  const { state, calls, hold } = await mountAutomation()
  hold()
  const second = state.loadDeliveries(2)
  const third = state.loadDeliveries(3)
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.deliveries.value[0].delivery_id, 'page-1')
  assert.equal(calls.emails[1].signal.aborted, true)
  calls.emails[2].resolve(); await third
  calls.emails[1].resolve(); await second
  assert.equal(state.deliveryPage.value, 3)
  assert.equal(state.deliveries.value[0].delivery_id, 'page-3')
  assert.equal(state.deliveryLoading.value, false)
  assert.deepEqual(calls.emails.map(call => call.pageSize), [10, 10, 10])
})

test('email paging: repeated clicks and invalid targets are ignored; failure keeps the applied page', async () => {
  const { state, calls, hold } = await mountAutomation()
  hold()
  const pending = state.changeDeliveryPage(2)
  state.changeDeliveryPage(3)
  assert.equal(calls.emails.length, 2)
  calls.emails[1].reject(new Error('email read failed')); await pending
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.deliveries.value[0].delivery_id, 'page-1')
  assert.match(state.error.value, /email read failed/)
  state.changeDeliveryPage(0); state.changeDeliveryPage(7); state.changeDeliveryPage(1)
  assert.equal(calls.emails.length, 2)
})

test('email paging: a shrinking final page is re-read at a valid page, including zero records', async () => {
  const { state, calls, hold } = await mountAutomation()
  await state.changeDeliveryPage(3)
  hold()
  const pending = state.loadDeliveries()
  calls.emails[2].resolve(11)
  await flushAutomation()
  assert.equal(calls.emails[3].page, 2)
  assert.equal(state.deliveryPage.value, 3, 'keep the valid old page until replacement succeeds')
  calls.emails[3].resolve(0)
  await flushAutomation()
  assert.equal(calls.emails[4].page, 1)
  calls.emails[4].resolve(0); await pending
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.pageCount.value, 1)
  assert.deepEqual(state.deliveries.value, [])
})

test('email paging: unmount cancels reads and ignores late failures', async () => {
  const { state, calls, hold, unmount } = await mountAutomation()
  hold()
  const pending = state.loadDeliveries(2)
  unmount()
  assert.equal(calls.emails[1].signal.aborted, true)
  calls.emails[1].reject(new Error('late')); await pending
  assert.equal(state.deliveryPage.value, 1)
  assert.equal(state.error.value, '')
})

test('automation delivery review preserves a dirty draft when switching is cancelled and rebinds refreshed records', async () => {
  let accepted = false
  const { state } = await mountAutomation({ confirm: async () => accepted })
  const original = { delivery_id: 'page-1', subject: '待核对记录' }
  await state.openDelivery(original)
  state.resolutionDirty.value = true
  await state.openDelivery({ delivery_id: 'other' })
  assert.equal(state.selectedDelivery.value.delivery_id, 'page-1')
  assert.equal(state.resolutionDirty.value, true)
  accepted = true
  await state.openDelivery({ delivery_id: 'other' })
  assert.equal(state.selectedDelivery.value.delivery_id, 'other')
  assert.equal(state.resolutionDirty.value, false)
  await state.openDelivery(original)
  await state.deliveryResolved()
  assert.equal(state.selectedDelivery.value, state.deliveries.value[0])
})

test('automation configuration keeps TLS modes exclusive and preserves empty-password save semantics', async () => {
  const saved = [], hasUnsavedChanges = ref(true)
  let refreshed = 0
  vi.spyOn(api, 'getAutomationConfiguration').mockResolvedValue({ smtp_password_configured: true, smtp_password: 'ignored-test-placeholder' })
  vi.spyOn(api, 'saveAutomationConfiguration').mockImplementation(async body => { saved.push(body); return { message: '已保存' } })
  const scope = effectScope()
  const state = scope.run(() => useAutomationConfiguration({ hasUnsavedChanges, refresh: async () => { refreshed++ } }))
  onTestFinished(() => scope.stop())
  await state.loadConfiguration()
  assert.equal(state.configuration.smtp_password, '')
  assert.equal(state.configuration.smtp_password_configured, true)
  assert.equal(hasUnsavedChanges.value, false)
  state.configuration.smtp_use_ssl = true
  state.selectSsl()
  assert.equal(state.configuration.smtp_use_tls, false)
  state.configuration.smtp_use_tls = true
  state.selectTls()
  assert.equal(state.configuration.smtp_use_ssl, false)
  await state.submitConfiguration()
  assert.equal(saved[0].smtp_password, null)
  state.configuration.smtp_password = 'new-test-password'
  await state.submitConfiguration()
  assert.equal(saved[1].smtp_password, 'new-test-password')
  assert.equal(state.configuration.smtp_password, '')
  assert.equal(refreshed, 2)
})


test.each([['leadership', 'current_month'], ['leadership', 'previous_month'], ['workshop', 'current_day'], ['equipment', 'previous_day']])(
  'editing the name of a %s schedule preserves its %s period', async (reportType, reportPeriod) => {
    const { state, calls } = await mountAutomation()
    const schedule = {
      schedule_id: 'schedule-1', device_id: 'Cutter02', name: 'Saved plan', report_type: reportType,
      report_period: reportPeriod, frequency: 'weekly', run_time: '16:30', weekday: 2, month_day: null,
      recipients: ['worker@example.com'], subject_template: '{report_name}', chart_type: 'line',
      attach_report: true, max_attempts: 3, enabled: true,
    }
    state.form.report_type = reportType === 'leadership' ? 'workshop' : 'leadership'
    state.editSchedule(schedule)
    assert.equal(state.form.report_period, reportPeriod)
    state.form.name = 'Renamed plan'
    await state.submit()
    const { schedule_id: id, ...payload } = schedule
    assert.deepEqual(calls.savedSchedules, [{ id, body: { ...payload, name: 'Renamed plan' } }])
  },
)

test('changing the schedule type resets only incompatible periods', async () => {
  const { state } = await mountAutomation()
  for (const [type, current, expected] of [
    ['equipment', 'current_day', 'current_day'], ['workshop', 'previous_day', 'previous_day'],
    ['leadership', 'current_month', 'current_month'], ['leadership', 'previous_month', 'previous_month'],
    ['leadership', 'current_day', 'previous_month'], ['workshop', 'current_month', 'previous_day'],
  ]) {
    Object.assign(state.form, { report_type: type, report_period: current })
    state.changeReportType()
    assert.equal(state.form.report_period, expected)
    assert.equal(state.hasUnsavedChanges.value, true)
  }
})
