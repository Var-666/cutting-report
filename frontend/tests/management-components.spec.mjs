import assert from 'node:assert/strict'
import { test } from 'vitest'
import { h } from 'vue'
import { renderComponent } from './vue-component-helpers.mjs'

test('execution progress renders stages, terminal errors and safe text without estimated percentages', async () => {
  for (const [status, label] of [['submitting', '正在提交任务'], ['queued', '等待后台准备报表'],
    ['generating', '正在生成报表'], ['sending', '正在发送邮件'], ['sent', 'SMTP 已接收'],
    ['failed', '执行失败'], ['unknown', '发送结果待核对'], ['cancelled', '已取消发送']]) {
    const html = await renderComponent('components/automation/AutomationRunProgress', {
      execution: { schedule_name: '<script>日报</script>', schedule_id: 'saved', status, last_error: '错误说明' },
    })
    assert.ok(html.includes(label), status)
    if (['submitting', 'queued', 'generating', 'sending'].includes(status)) {
      assert.ok(html.includes('提交任务') && html.includes('生成报表') && html.includes('发送邮件'))
    }
    assert.ok(html.includes('&lt;script&gt;日报&lt;/script&gt;'))
    assert.ok(html.includes('错误说明'))
    assert.doesNotMatch(html, /\d+(?:\.\d+)?%<\//)
  }
})

test('automation schedule cards show task details and cancellation for its run', async () => {
  const schedule = { schedule_id: 'schedule', enabled: true, name: '每日车间', device_id: 'Cutter01', report_type: 'workshop', frequency: 'daily', run_time: '18:00', recipients: ['one@example.com'], sent_count: 12, failed_count: 1 }
  const html = await renderComponent('components/automation/AutomationScheduleCards', {
    schedules: [schedule], runningScheduleIds: ['schedule'], cancellableScheduleIds: ['schedule'],
    executions: { schedule: { schedule_id: 'schedule', schedule_name: schedule.name, status: 'generating' } },
    reportLabels: { workshop: '车间报表' }, scheduleLabels: {},
  })
  for (const value of ['每日车间', '20KW切割机', '每日 18:00', '执行中', '取消发送', '编辑', '暂停', 'aria-label="删除 每日车间"']) assert.ok(html.includes(value))
  assert.doesNotMatch(html, /<table/)
  const paused = await renderComponent('components/automation/AutomationScheduleCards', {
    schedules: [{ ...schedule, enabled: false, next_run_at: '2026-11-20T18:00:00+08:00' }], reportLabels: { workshop: '车间报表' }, scheduleLabels: {},
  })
  assert.match(paused, />暂停</)
  assert.match(paused, />恢复</)
  assert.doesNotMatch(paused, /<time|已暂停/)
})

test('automation recipients appear once and keep accepted recipients after partial refusal', async () => {
  const delivery = { delivery_id: 'd1', schedule_name: '日报', subject: '自定义主题', recipients: ['retry@example.com'],
    recipient_results: { 'accepted@example.com': { status: 'accepted' }, 'retry@example.com': { status: 'refused', code: 450 } }, status: 'failed' }
  const html = await renderComponent('components/automation/AutomationDeliveryTable', {
    deliveries: [delivery],
    deliveryTotal: 1, deliveryPage: 1, deliveryLoading: false, paginate: true, deliveryLabels: { failed: '发送失败' },
  })
  assert.ok(html.includes('查看全部 2 位收件人'))
  const expanded = await renderComponent('components/automation/AutomationRecipientTags', { delivery })
  assert.equal((expanded.match(/>retry@example.com</g) || []).length, 1)
  assert.equal((expanded.match(/>accepted@example.com</g) || []).length, 1)
  assert.match(expanded, /title="accepted@example.com"/)
  assert.doesNotMatch(expanded, /服务器已接受|>已接收</)
  assert.match(expanded, /服务器拒收 450/)
  assert.ok(html.includes('title="自定义主题"'))
  assert.doesNotMatch(html, /<small[^>]*>自定义主题/)
})

test('management heading renders an accessible title and optional controls', async () => {
  const props = { title: '数据库配置' }
  const html = await renderComponent('components/ManagementPageHero', props, { actions: () => h('button', '刷新连接状态') })
  assert.match(html, /<h1[^>]*>数据库配置<\/h1>/)
  assert.match(html, /<button>刷新连接状态<\/button>/)
  assert.doesNotMatch(await renderComponent('components/ManagementPageHero', props), /刷新连接状态/)
})
