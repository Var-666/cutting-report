// @vitest-environment jsdom

import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { NButton, NRadioButton, NRadioGroup } from 'naive-ui'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { getAlarmStatus, listAlarmIncidentPage, listAlarmReportPage, listAlarmReports } from '../src/api/alarms'
import AlarmCenterView from '../src/views/AlarmCenterView.vue'

vi.mock('../src/api/alarms')

enableAutoUnmount(afterEach)
beforeEach(() => {
  vi.mocked(getAlarmStatus).mockResolvedValue({ enabled: true, worker_running: true, machine_nos: ['Cutter01'] })
  vi.mocked(listAlarmIncidentPage).mockResolvedValue({ items: [], page: 1, all_total: 0, severity_counts: {} })
  vi.mocked(listAlarmReportPage).mockResolvedValue({ items: [], page: 1, total: 0, completed_count: 0, page_count: 1 })
  vi.mocked(listAlarmReports).mockResolvedValue([])
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})

function openAlarmCenter() {
  return mount(AlarmCenterView, { global: { components: { NButton, NRadioButton, NRadioGroup } } })
}

test('completed alarms display their evidence and PDF actions while failed reports have no file link', async () => {
  const incident = { incident_id: 'incident', machine_no: 'Cutter01', severity: 'warning', status: 'open', alarm_desc: '水冷机报警', pdf_report_id: 'report', report_status: 'completed', report_view_url: '/api/reports/Cutter01/view' }
  const report = {
    report_id: 'report', machine_no: 'Cutter01', incident_ids: ['incident'], status: 'completed', model_status: 'model',
    pdf_filename: 'Cutter01_智能告警报告_20261007.pdf',
    download_url: '/api/reports/Cutter01/download', view_url: '/api/reports/Cutter01/view',
    analysis: {
      overall_risk: 'warning', summary: 'Cutter01出现水冷机报警 [E001]',
      findings: [{ title: 'Cutter01水冷机报警', analysis: '检查Cutter01冷却系统', possible_causes: ['Cutter01冷却异常'], confidence: 0.6, evidence_ids: ['E001'] }],
      actions: [{ action: '检查Cutter01冷却水', evidence_ids: ['E001'] }],
      facts: [{ id: 'E001', machine_no: 'Cutter01', alarm_desc: '水冷机报警' }], limitations: ['Cutter01缺少维修记录'],
    },
  }
  const originalReport = structuredClone(report)
  const failed = { report_id: 'failed', machine_no: 'Cutter01', incident_ids: ['incident'], status: 'failed',
    created_at: '2026-10-08T09:00:00+08:00', error_message: '模型接口限流' }
  vi.mocked(listAlarmIncidentPage).mockResolvedValue({ items: [incident], page: 1, severity_counts: {} })
  vi.mocked(listAlarmReports).mockResolvedValue([report])
  vi.mocked(listAlarmReportPage).mockResolvedValue({ items: [report, failed], page: 1, total: 2, page_count: 1 })
  const wrapper = openAlarmCenter()
  await flushPromises()
  const text = wrapper.text()
  expect(text).not.toContain('Cutter01')
  expect(text).toContain('20KW切割机_智能告警报告_20261007.pdf')
  expect(text).toContain('检查20KW切割机冷却系统')
  expect(text).toContain('[E001]')
  expect(text).toContain('置信度 60%')
  const table = wrapper.get('[aria-label="实体告警报告"]')
  expect(table.get('a[href="/api/reports/Cutter01/download"]').exists()).toBe(true)
  expect(table.get('a[href="/api/reports/Cutter01/view"]').exists()).toBe(true)
  const failedRow = table.findAll('tbody tr').find(row => row.text().includes('模型接口限流'))
  expect(failedRow.text()).toContain('20KW切割机_告警报告_20261008')
  expect(failedRow.text()).toContain('生成失败')
  expect(failedRow.find('a').exists()).toBe(false)
  expect(report).toEqual(originalReport)
})

test('incident links follow independent report status even for an older report outside the recent list', async () => {
  const cases = [
    { incident_id: 'old-completed', pdf_report_id: 'old-report', report_status: 'completed', report_view_url: '/api/v1/alarms/reports/old-report/view', label: '查看' },
    { incident_id: 'generating', pdf_report_id: 'generating-report', report_status: 'generating', report_view_url: '/not-ready', label: '生成中' },
    { incident_id: 'failed', pdf_report_id: 'failed-report', report_status: 'failed', report_error_message: '模型服务不可用', label: '生成失败' },
    { incident_id: 'pending', pdf_report_id: null, label: '待生成' },
    { incident_id: 'missing', pdf_report_id: 'missing-report', report_status: null, label: '报告不存在' },
    { incident_id: 'no-file', pdf_report_id: 'no-file-report', report_status: 'completed', report_view_url: null, label: '报告不可用' },
  ]
  const items = cases.map((item) => ({ ...item, machine_no: 'Cutter01', severity: 'warning', status: 'open' }))
  vi.mocked(listAlarmIncidentPage).mockResolvedValue({ items, page: 1, severity_counts: {} })
  const wrapper = openAlarmCenter()
  await flushPromises()
  const rows = wrapper.get('[aria-label="切割机告警事件"]').findAll('tbody tr')
  expect(rows).toHaveLength(cases.length)
  for (const [index, item] of cases.entries()) {
    expect(rows[index].text()).toContain(item.label)
    expect(rows[index].text()).toContain('异常')
    expect(rows[index].text()).toContain('待处理')
    expect(rows[index].find('a').exists()).toBe(item.incident_id === 'old-completed')
  }
  expect(rows[0].get('a').attributes('href')).toBe('/api/v1/alarms/reports/old-report/view')
  expect(rows[2].get('[title="模型服务不可用"]').text()).toContain('生成失败')
  expect(wrapper.text()).not.toContain('已归档')
})

test('leaving during initialization aborts the request and installs no later timers', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] })
  try {
    let finish, signal
    vi.mocked(getAlarmStatus).mockImplementation(({ signal: current }) => {
      signal = current
      return new Promise(resolve => { finish = resolve })
    })
    const wrapper = openAlarmCenter()
    expect(wrapper.text()).toContain('正在读取')
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    finish({ enabled: true })
    await flushPromises()
    expect(vi.getTimerCount()).toBe(0)
  } finally {
    vi.useRealTimers()
  }
})
