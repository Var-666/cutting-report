import { runTypeScript } from './typescript-helpers.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { createActivationTimer } from '../src/utils/activationTimer.ts'
import { renderComponent } from './vue-component-helpers.mjs'

const source = readFileSync(new URL('../src/components/AnalysisPanel.vue', import.meta.url), 'utf8')

test('historical analysis remains visible with its cache label and update feedback', async () => {
  const html = await renderComponent('components/ReportAnalysis', { analysis: {
    status: 'success', mode: 'model', overview: '已有成功分析。生产记录需要核对。', features: '', alerts: '', suggestions: '',
    facts: [], cached: true, cache_note: '沿用历史成功分析，原生成时间保持不变。', generated_at: '2026-11-12T09:55:00+08:00',
  }, error: '本次更新正在等待模型', ready: true, loading: false })
  assert.match(html, /已有成功分析/)
  assert.match(html, /沿用历史成功分析/)
  assert.match(html, /09:55:00/)
  assert.match(html, /已缓存/)
  assert.match(html, /本次更新正在等待模型/)
  assert.doesNotMatch(html, /智能分析暂不可用/)
})

test('visible analysis checks every five minutes, refreshes only changed data and stops when deactivated', async () => {
  const props = { reportType: 'leadership', reportDate: '2026-10-19', ready: true, dataVersion: 'v1', versionCheckEnabled: true }
  const intervals = new Map(), events = []
  let activate, deactivate, version = 'v1.v1', analysisCalls = 0, latestReads = 0, versionChecks = 0
  const context = vm.createContext({
    defineProps: () => props, defineEmits: () => event => events.push(event),
    ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    watch() {}, onActivated: fn => { activate = fn }, onDeactivated: fn => { deactivate = fn }, onBeforeUnmount() {},
    getDeviceId: () => 'Cutter01', subscribeDevices: () => () => {}, createActivationTimer,
    analyzeReport: async () => { analysisCalls += 1; return { data_version: 'v1.v1' } },
    getLatestAnalysis: async () => { latestReads += 1; return { data_version: 'v1.v1' } },
    getAnalysisVersion: async () => { versionChecks += 1; return { data_version: version } },
    window: {
      setInterval: (callback, delay) => { const id = intervals.size + 1; intervals.set(id, { callback, delay }); return id },
      clearInterval: id => intervals.delete(id),
    },
    document: { visibilityState: 'visible' }, AbortController, console,
  })
  runTypeScript(source.match(/<script setup(?: lang="ts")?>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, ''), context)
  activate()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(intervals.size, 2)
  assert.equal(intervals.get(1).delay, 5 * 60 * 1000)
  assert.equal(analysisCalls, 0)
  assert.equal(latestReads, 1)
  const tick = intervals.get(1).callback
  tick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(versionChecks, 1)
  assert.deepEqual(events, [])
  version = 'v2.v2'
  tick()
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(events, ['refresh-report'])
  assert.equal(analysisCalls, 0, 'default page reads must never generate a duplicate model call')
  deactivate()
  assert.equal(intervals.size, 0)
  tick()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(versionChecks, 2, 'a deactivated page must stop checking the source')
})

function panelFixture({ defaultView = true, read, analyze } = {}) {
  const props = { reportType: 'workshop', reportDate: '2026-10-21', ready: true, dataVersion: 'v1', versionCheckEnabled: defaultView }
  let device = 'Cutter01', subscriber
  const reads = [], calls = []
  let now = 1_000_000
  const context = vm.createContext({
    defineProps: () => props, defineEmits: () => () => {},
    ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    watch() {}, onActivated() {}, onDeactivated() {}, onBeforeUnmount() {},
    getDeviceId: () => device, subscribeDevices: fn => { subscriber = fn; return () => {} },
    createActivationTimer: () => ({ isActive: () => true, cancel() {} }),
    getLatestAnalysis: (query, options) => { reads.push({ query, options }); return read(query, options) },
    analyzeReport: (query, options) => { calls.push({ query, options }); return analyze(query, options) },
    document: { visibilityState: 'visible' }, AbortController, console, Date: { now: () => now },
  })
  runTypeScript(source.match(/<script setup(?: lang="ts")?>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
    + '\nthis.panel = { loadAnalysis, scheduleAutomaticAnalysis, pollLatestAnalysis, reloadAnalysis, retryAnalysis, analysis, loading, readingLatest, error }', context)
  return { ...context.panel, props, reads, calls, advance(ms) { now += ms }, switchDevice(id) { device = id; subscriber() } }
}

const flush = () => new Promise(resolve => setImmediate(resolve))
const result = (time, overview = time) => ({ overview, generated_at: `2026-10-21T${time}:00+08:00`, data_version: 'v1.v1', cached: true })

test('entering a default page displays the previous success and polls for the next completed result', async () => {
  let latest = result('09:55')
  const panel = panelFixture({ read: async () => latest })
  panel.scheduleAutomaticAnalysis()
  assert.equal(panel.readingLatest.value, true, 'reading cached content must have a separate loading state')
  await flush()
  assert.equal(panel.analysis.value.overview, '09:55')
  latest = result('10:00')
  panel.pollLatestAnalysis()
  assert.equal(panel.analysis.value.overview, '09:55', 'the old result must remain visible while the read is pending')
  await flush()
  assert.equal(panel.analysis.value.overview, '10:00', 'a new completion must replace old wording even when source data is unchanged')
  assert.equal(panel.calls.length, 0)
  assert.equal(panel.reads.length, 2)
})

test('an empty default cache explicitly prepares the first analysis once', async () => {
  let latest = null
  const panel = panelFixture({ read: async () => latest, analyze: async () => result('09:55') })
  await panel.loadAnalysis()
  assert.equal(panel.analysis.value.overview, '09:55')
  assert.equal(panel.loading.value, false)
  latest = result('10:00')
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.analysis.value.overview, '10:00')
  assert.equal(panel.calls.length, 1)
})

test('a failed result read keeps the last success and retry still only reads the cache', async () => {
  let failed = false
  const panel = panelFixture({ read: async () => { if (failed) throw new Error('offline'); return result('09:55') } })
  await panel.loadAnalysis()
  failed = true
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.error.value, 'offline')
  assert.equal(panel.analysis.value.overview, '09:55')
  failed = false
  await panel.retryAnalysis()
  assert.equal(panel.error.value, '')
  assert.equal(panel.calls.length, 0)
})

test('manual refresh calls the model and an older background snapshot cannot replace it', async () => {
  const panel = panelFixture({ read: async () => result('09:55'), analyze: async () => result('10:01') })
  await panel.loadAnalysis()
  await panel.reloadAnalysis()
  assert.equal(panel.calls.length, 1)
  assert.equal(panel.calls[0].query.force, true)
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.analysis.value.overview, '10:01')
})

test('switching device or day aborts an old snapshot read and prevents its late result from returning', async () => {
  const pending = []
  const panel = panelFixture({ read: () => new Promise(resolve => pending.push(resolve)), analyze: async () => null })
  panel.scheduleAutomaticAnalysis()
  panel.switchDevice('Cutter02')
  panel.scheduleAutomaticAnalysis()
  assert.equal(panel.reads[0].options.signal.aborted, true)
  pending[1](result('10:00', 'device two'))
  await flush()
  pending[0](result('10:01', 'device one'))
  await flush()
  assert.equal(panel.analysis.value.overview, 'device two')
  panel.props.reportDate = '2026-10-20'
  panel.scheduleAutomaticAnalysis()
  assert.equal(panel.analysis.value, null, 'a different report day must never show the previous day analysis')
  pending[2](null)
  await flush()
})

test('custom report queries retain on-demand analysis rather than taking the default background snapshot', async () => {
  let answer = result('10:00', 'custom range')
  const panel = panelFixture({ defaultView: false, analyze: async () => answer })
  await panel.loadAnalysis()
  panel.pollLatestAnalysis()
  assert.equal(panel.analysis.value.overview, 'custom range')
  assert.equal(panel.reads.length, 0)
  assert.equal(panel.calls.length, 1)
  assert.equal(panel.calls[0].query.force, false)
  answer = result('09:55', 'matching cached input after a data correction')
  await panel.loadAnalysis()
  assert.equal(panel.analysis.value.overview, answer.overview, 'a custom query must use its matching input cache even if it was generated earlier')
})

for (const defaultView of [true, false]) test(`cold analysis retries after backoff instead of every poll (default=${defaultView})`, async () => {
  let unavailable = true
  const panel = panelFixture({ defaultView, read: async () => null, analyze: async () => {
    if (unavailable) throw Object.assign(new Error('正在等待成功的智能分析结果'), { status: 503, retryAfterMs: 60_000 })
    return result('10:00')
  } })
  await panel.loadAnalysis()
  assert.equal(panel.calls.length, 1)
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.calls.length, 1)
  panel.advance(60_000)
  unavailable = false
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.calls.length, 2)
  assert.equal(panel.analysis.value.overview, '10:00')
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.calls.length, 2, 'an existing successful result must survive an empty latest read')
})

test('waiting report and automation progress keep waiting visible without download or mail completion', async () => {
  const job = await renderComponent('components/ReportJobStatus', { job: { report_id: 'waiting', report_type: 'leadership', status: 'pending',
    stage_message: '等待智能分析完成，系统将自动重试', ai_status: 'waiting' } })
  assert.match(job, /等待智能分析完成/)
  assert.doesNotMatch(job, /打开报告|下载 HTML|暂不可用/)
  const execution = await renderComponent('components/automation/AutomationRunProgress', { execution: {
    schedule_id: 'schedule', schedule_name: '每日生产报告', status: 'analysis_wait',
  } })
  assert.match(execution, /等待智能分析成功，后台将自动重试/)
  assert.doesNotMatch(execution, /邮件已发送|执行失败/)
})

test('same-query update and failure retain the previous result; device or range changes clear it', async () => {
  const props = { reportType: 'leadership', reportDate: '2026-10-14', ready: true, dataVersion: 'v1' }
  let device = 'Cutter01', subscriber, reject
  let calls = 0
  const context = vm.createContext({
    defineProps: () => props, defineEmits: () => () => {},
    ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    watch() {}, onActivated() {}, onDeactivated() {}, onBeforeUnmount() {},
    getDeviceId: () => device, subscribeDevices: fn => { subscriber = fn; return () => {} },
    createActivationTimer: () => ({ isActive: () => true, cancel() {} }),
    analyzeReport: () => ++calls === 1 ? Promise.resolve({ overview: 'previous', generated_at: '2026-10-14T10:00:00' }) : new Promise((_, fail) => { reject = fail }),
    AbortController, console,
  })
  runTypeScript(source.match(/<script setup(?: lang="ts")?>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '') + '\nthis.panel = { loadAnalysis, scheduleAutomaticAnalysis, analysis, error }', context)
  const panel = context.panel
  await panel.loadAnalysis()
  props.dataVersion = 'v2'
  panel.scheduleAutomaticAnalysis()
  assert.equal(panel.analysis.value.overview, 'previous')
  reject(new Error('temporary failure'))
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(panel.analysis.value.overview, 'previous')
  assert.equal(panel.error.value, 'temporary failure')
  props.periodStart = '2026-09-01'
  panel.scheduleAutomaticAnalysis()
  assert.equal(panel.analysis.value, null)
  device = 'Cutter02'
  subscriber()
  panel.scheduleAutomaticAnalysis()
  assert.equal(panel.analysis.value, null)
})
