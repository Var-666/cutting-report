import { runTypeScript } from './typescript-helpers.mjs'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { createActivationTimer } from '../src/utils/activationTimer.ts'

const source = readFileSync(new URL('../src/components/AnalysisPanel.vue', import.meta.url), 'utf8')

test('analysis omits the previous-result notice and retains loading and error feedback', () => {
  const presentation = readFileSync(new URL('../src/components/ReportAnalysis.vue', import.meta.url), 'utf8')
  const template = presentation.match(/<template>([\s\S]*?)<\/template>/)[1]
  assert.doesNotMatch(template, /上次分析时间|此结果基于上次数据|后台新结果完成后自动更新|等待更新/)
  assert.match(template, /v-if="loading && !analysis" class="analysis-state"/)
  const errorAlert = template.match(/<NAlert\b[^>]*v-else-if="error"[^>]*>/)?.[0] || ''
  assert.match(errorAlert, /class="analysis-error"/)
  assert.match(errorAlert, /type="error"/)
  assert.match(errorAlert, /role="alert"/)
  assert.match(template, /<template v-if="analysis">/)
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
  const context = vm.createContext({
    defineProps: () => props, defineEmits: () => () => {},
    ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
    watch() {}, onActivated() {}, onDeactivated() {}, onBeforeUnmount() {},
    getDeviceId: () => device, subscribeDevices: fn => { subscriber = fn; return () => {} },
    createActivationTimer: () => ({ isActive: () => true, cancel() {} }),
    getLatestAnalysis: (query, options) => { reads.push({ query, options }); return read(query, options) },
    analyzeReport: (query, options) => { calls.push({ query, options }); return analyze(query, options) },
    document: { visibilityState: 'visible' }, AbortController, console,
  })
  runTypeScript(source.match(/<script setup(?: lang="ts")?>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '')
    + '\nthis.panel = { loadAnalysis, scheduleAutomaticAnalysis, pollLatestAnalysis, reloadAnalysis, retryAnalysis, analysis, loading, readingLatest, error }', context)
  return { ...context.panel, props, reads, calls, switchDevice(id) { device = id; subscriber() } }
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

test('an empty default cache waits for the background result and never falls back to a page model call', async () => {
  let latest = null
  const panel = panelFixture({ read: async () => latest })
  await panel.loadAnalysis()
  assert.equal(panel.analysis.value, null)
  assert.equal(panel.loading.value, false)
  latest = result('10:00')
  panel.pollLatestAnalysis()
  await flush()
  assert.equal(panel.analysis.value.overview, '10:00')
  assert.equal(panel.calls.length, 0)
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
  const panel = panelFixture({ read: () => new Promise(resolve => pending.push(resolve)) })
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
