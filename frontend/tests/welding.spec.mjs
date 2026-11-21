import { renderComponent } from './vue-component-helpers.mjs'
import { afterEach, beforeEach, test, vi } from 'vitest'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { lineHome, productionGroups, selectedLineType } from '../src/utils/productionLines.ts'
import { adminMenus } from '../src/router/menus.ts'
import { analyzeWelding, getLatestWeldingAnalysis, downloadWelding, getWeldingPart, getWeldingReport, weldingQuery } from '../src/api/welding.ts'
import { weldingColumns, weldingColumnLayout, weldingDisplay, weldingKpis } from '../src/utils/weldingReport.ts'
import { weldingRoutes } from '../src/router/weldingRoutes.ts'
import { setDeviceCatalog, selectDevice } from '../src/api/deviceContext.ts'
import { reportCache } from '../src/api/reportCache.ts'
import { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT } from '../src/api/reportRequests.ts'
import { parseWeldingRouteQuery, weldingRouteQuery } from '../src/utils/weldingRouteState.ts'

beforeEach(() => setDeviceCatalog({ default_device_id: 'welding', items: [{ id: 'welding', name: '焊接', type: 'welding' }] }))
afterEach(() => { vi.useRealTimers(); reportCache.clear() })

test('welding KPI layouts preserve zero, missing and pending measurements with their scope notes', async () => {
  setActivePinia(createPinia())
  for (const variant of ['home', 'report']) {
    const html = await renderComponent('components/HomeKpiCard', {
      title: '焊接长度', value: 0, unit: 'm', precision: 2, variant,
      trendLabel: '仅使用长度、用时均有效的作业',
    })
    assert.match(html, /aria-label="焊接长度：0\.00 m"/)
    assert.ok(html.includes('仅使用长度、用时均有效的作业'))
    assert.ok(!html.includes('暂无趋势'))
    const missing = await renderComponent('components/HomeKpiCard', { title: '焊接长度', value: null, unit: 'm', variant })
    assert.match(missing, /aria-label="焊接长度：暂无数据"/)
    assert.ok(!missing.includes('>m<'))
    const loading = await renderComponent('components/HomeKpiCard', { title: '焊接长度', value: null, unit: 'm', loading: true, variant })
    assert.match(loading, /aria-label="焊接长度：正在加载"/)
    assert.match(loading, /aria-label="正在获取生产数据"/)
  }
  const pending = await renderComponent('components/WeldingKpiCards', {
    kind: 'parts', loading: false,
    report: { summary: { involved_part_count: 3, part_row_count: 4, part_actual_length_m: null }, daily: [],
      meta: { pending_metrics: { part_actual_length_m: '工件归属待核实' }, ambiguous_part_count: 1 } },
  })
  assert.ok(pending.includes('累计实际焊接长度：待核实 m'))
  assert.ok(pending.includes('口径待核实'))
  assert.ok(pending.includes('工件累计值；归属不明确时待核实'))
  assert.ok(pending.includes('参与流水号存在多条工件归属'))
})

test('device types contain independent welding lines without adding a third navigation level', () => {
  const items = [{ id: 'Cutter01', type: 'cutting', name: '20KW' }, { id: 'Cutter02', type: 'cutting', name: '30KW' }, { id: 'welding', type: 'welding', name: '小组立焊接线' }, { id: 'other-welding', type: 'welding', name: '另一焊接线' }]
  const groups = productionGroups(items)
  assert.deepEqual(groups.map(group => [group.name, group.items.length]), [['切割', 2], ['焊接', 2]])
  assert.equal(selectedLineType(items, 'other-welding'), 'welding')
  assert.equal(selectedLineType(items, 'Cutter01'), 'cutting')
})

test('business menus isolate cutting capabilities and flatten the welding layout', () => {
  const routes = [{ path: 'alarms', name: 'alarms', meta: { line: 'cutting', title: '告警' } }, weldingRoutes,
    { path: 'settings', meta: { title: '设置' }, children: [{ path: 'database', name: 'database', meta: { title: '数据库' } }, { path: 'automation', name: 'automation', meta: { title: '自动化', line: 'cutting' } }] }]
  const menus = adminMenus(routes, '/admin', 'welding')
  assert.ok(menus.some(menu => menu.key === 'welding-home'))
  assert.ok(menus.some(menu => menu.key === '/admin/welding/reports' && menu.children.length === 4))
  const text = JSON.stringify(menus)
  assert.ok(!text.includes('alarms') && !text.includes('automation'))
  assert.ok(JSON.stringify(adminMenus(routes)).includes('alarms'))
  assert.ok(!JSON.stringify(adminMenus(routes)).includes('welding-home'))
  assert.ok(!menus.some(menu => menu.label === '智能分析'))
  const legacyAnalysis = weldingRoutes.children.find(route => route.path === 'analysis')
  assert.deepEqual(legacyAnalysis.redirect({ query: { start: '2026-09-01', end: '2026-09-02' } }), {
    path: '/admin/welding/reports/daily', query: { start: '2026-09-01', end: '2026-09-02' },
  })
})

test('query parameters retain zero device, robot and process codes; unknown is not zero', () => {
  assert.deepEqual(weldingQuery({ device_no: 0, robot_no: 0, weld_type: 0, state: '', project_no: null, anomalies_only: false }), { device_no: 0, robot_no: 0, weld_type: 0, anomalies_only: false })
  assert.equal(weldingDisplay(null, 'completed_part_count'), '待核实')
  assert.equal(weldingDisplay(0, 'weld_length_m'), '0.000')
  assert.equal(weldingDisplay(null, 'weld_length_m'), '未记录')
  const cards = weldingKpis('daily', { summary: { weld_length_m: 0, involved_part_count: 0 }, meta: { pending_metrics: { completed_part_count: 'pending' } } })
  assert.equal(cards[1].value, 0)
  assert.equal(cards[2].value, 0)
  assert.equal(cards[3].value, null)
  const parts = weldingKpis('parts', { summary: { part_row_count: 0, part_actual_length_m: null }, meta: { ambiguous_part_count: 2, pending_metrics: { part_actual_length_m: '歧义关联' } } })
  assert.equal(parts[1].value, 0)
  assert.equal(parts[2].value, '待核实')
  assert.equal(parts[3].value, 2)
})

test('equivalent query defaults and field order share one HTTP request while filters stay isolated', async t => {
  let finish
  const gate = new Promise(resolve => { finish = resolve })
  const calls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
    calls.push(url)
    await gate
    return new Response(JSON.stringify({ success: true, data: { data_version: 'shared' } }), { headers: { 'Content-Type': 'application/json' } })
  })
  const first = getWeldingReport('daily', { start: '2026-10-01', end: '2026-10-02' })
  const second = getWeldingReport('daily', { page_size: 20, page: 1, end: '2026-10-02', start: '2026-10-01', descending: true, sort_by: 'time', anomalies_only: false })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls.length, 1)
  finish()
  assert.equal(await first, await second)
  await getWeldingReport('daily', { start: '2026-10-01', end: '2026-10-02', device_no: 0 })
  assert.equal(calls.length, 2)
  await getWeldingReport('daily', { device_no: '0', end: '2026-10-02', start: '2026-10-01' })
  assert.equal(calls.length, 2)
})

test('a successful JSON response cannot be downloaded as a PDF', async t => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { headers: { 'Content-Type': 'application/json' } }))
  await assert.rejects(downloadWelding('/api/v1/welding/export', 'test.pdf'), /文件格式不正确/)
})

const period = { start: '2026-10-26', end: '2026-10-26' }
const busyResponse = (retryAfter = '1') => Response.json({ detail: '焊接数据查询请求繁忙，请稍后重试。' }, { status: 503, headers: { 'Retry-After': retryAfter } })
const settle = () => new Promise(resolve => setImmediate(resolve))

const preparingResponse = () => Response.json({ detail: '统计正在生成，请稍后重试' }, { status: 503, headers: { 'Retry-After': '1', 'X-Welding-Error': 'snapshot_refresh_busy' } })

test('welding route parsing validates all boundaries, preserves numeric zero and drops incompatible filters', () => {
  const all = { start: '2026-09-01', end: '2026-09-02', device_no: '0', robot_no: '0', weld_type: '0', state: '0', part_state: '0', serial_number: 'S', project_no: 'P', section_no: 'SEC', part_no: 'PART', anomalies_only: 'true', sort_by: 'length', descending: 'false', page: '3', page_size: '50', unrelated: 'keep' }
  const jobs = parseWeldingRouteQuery(all, 'jobs', '2026-10-26')
  assert.deepEqual([jobs.device_no, jobs.robot_no, jobs.weld_type, jobs.state], [0, 0, 0, 0])
  assert.equal(jobs.anomalies_only, true)
  assert.equal(jobs.descending, false)
  const parts = parseWeldingRouteQuery(all, 'parts', '2026-10-26')
  assert.deepEqual([parts.project_no, parts.section_no, parts.part_no, parts.part_state], ['P', 'SEC', 'PART', 0])
  assert.equal(parts.robot_no, '')
  for (const kind of ['daily', 'equipment', 'dashboard']) {
    const parsed = parseWeldingRouteQuery(all, kind, '2026-10-26')
    assert.equal(parsed.serial_number, '')
    assert.equal(parsed.project_no, '')
    assert.equal(parsed.anomalies_only, false)
    assert.equal(parsed.robot_no, kind === 'equipment' ? 0 : '')
  }
  for (const value of ['', ' ', '0x10', '1e2', '1.2', '-1', '9007199254740992', ['0', '1'], null]) {
    const parsed = parseWeldingRouteQuery({ ...all, device_no: value, robot_no: value }, 'jobs', '2026-10-26')
    assert.equal(parsed.device_no, '', `invalid device ${value}`)
    assert.equal(parsed.robot_no, '')
  }
  const invalid = parseWeldingRouteQuery({ start: '2025-01-01', end: '2026-10-26', serial_number: '\u0000bad', page: '100001', page_size: '101', state: 'no', sort_by: 'bad', anomalies_only: '1', descending: '0' }, 'jobs', '2026-10-26')
  assert.deepEqual([invalid.start, invalid.end, invalid.page, invalid.page_size, invalid.state, invalid.serial_number, invalid.sort_by, invalid.anomalies_only, invalid.descending], ['2026-10-26', '2026-10-26', 1, 10, '', '', 'time', false, true])
  const query = weldingRouteQuery(all, jobs)
  assert.equal(query.unrelated, 'keep')
  assert.equal(query.device_no, '0')
  assert.equal(query.anomalies_only, 'true')
  assert.equal(query.descending, 'false')
  assert.equal('project_no' in query, false)
  assert.deepEqual(parseWeldingRouteQuery(query, 'jobs', '2026-10-26'), jobs)
  for (const [source, expected] of [
    [{ state: '0' }, 0], [{ state: '3', part_state: '0' }, 0], [{ state: '3', part_state: '4' }, 4],
    [{ state: '3', part_state: '' }, ''], [{ state: '3', part_state: 'bad' }, ''],
  ]) {
    const restored = parseWeldingRouteQuery(source, 'parts', '2026-10-26')
    assert.equal(restored.part_state, expected)
    const canonical = weldingRouteQuery(source, restored)
    assert.equal(canonical.part_state, expected === '' ? undefined : String(expected))
    assert.equal('state' in canonical, false)
  }
})

test('source, authentication and network failures remain visible without replay', async t => {
  const responses = [
    () => Response.json({ detail: '数据源超时' }, { status: 503 }),
    () => Response.json({ detail: '数据源连接池已满' }, { status: 503, headers: { 'X-Report-Error': 'connection_pool_exhausted' } }),
    () => Response.json({ detail: '权限不足' }, { status: 403, headers: { 'Retry-After': '1' } }),
    () => busyResponse('invalid'),
    () => busyResponse('0'),
    () => busyResponse('-1'),
    () => { throw new TypeError('network failure') },
  ]
  for (const response of responses) {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => response())
    await assert.rejects(getWeldingReport('jobs', period))
    assert.equal(fetchMock.mock.calls.length, 1)
    fetchMock.mockRestore()
  }
})

test('latest welding analysis reads cache with the applied scope and cancellation without a model request', async t => {
  const current = new AbortController()
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (url, options) => {
    const parsed = new URL(url, 'http://localhost')
    assert.equal(parsed.pathname, '/api/v1/welding/analysis/latest')
    assert.equal(options.method || 'GET', 'GET')
    assert.equal(options.headers['X-Production-Line'], 'welding')
    assert.equal(options.signal, current.signal)
    assert.equal(parsed.searchParams.get('device_no'), '0')
    assert.equal(parsed.searchParams.get('report_type'), 'jobs')
    assert.equal(parsed.searchParams.get('data_version'), 'a'.repeat(64))
    assert.ok(!parsed.searchParams.has('serial_number'))
    return new Response(JSON.stringify({ success: true, data: null }), { headers: { 'Content-Type': 'application/json' } })
  })
  assert.equal(await getLatestWeldingAnalysis('jobs', { ...period, device_no: 0, serial_number: '' }, 'a'.repeat(64), current.signal), null)
  assert.equal(fetchMock.mock.calls.length, 1)
})

test('reports, details, downloads and analysis never replay a capacity rejection', async t => {
  for (const read of [
    ...['dashboard', 'daily', 'parts', 'jobs', 'equipment'].map(kind => () => getWeldingReport(kind, period)),
    () => getWeldingPart(1, 1),
    () => downloadWelding('/api/v1/welding/export', 'report.pdf'),
    () => analyzeWelding('jobs', period, 'version'),
    () => getLatestWeldingAnalysis('jobs', period, 'version'),
  ]) {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => busyResponse())
    await assert.rejects(read(), /请求繁忙/)
    assert.equal(fetchMock.mock.calls.length, 1)
    fetchMock.mockRestore()
  }
})

test('new line types keep registered labels and destinations', () => {
  const items = [{ id: 'paint-1', type: 'coating', type_name: '涂装', name: '涂装一线', home_path: '/admin/coating/home' }]
  assert.equal(productionGroups(items)[0].name, '涂装')
  assert.equal(selectedLineType(items, 'paint-1'), 'coating')
  assert.equal(lineHome(items, 'paint-1'), '/admin/coating/home')
  assert.equal(productionGroups([{ ...items[0], type_name: '' }])[0].name, 'coating')
})

test('welding measurements keep fixed decimals while counts and identifiers retain their meaning', () => {
  for (const key of ['weld_length_m', 'working_hours', 'weld_speed', 'arc_rate', 'duration_seconds', 'wire_consumption']) {
    assert.equal(weldingDisplay(12.5, key), '12.500')
    assert.equal(weldingDisplay(0, key), '0.000')
  }
  assert.equal(weldingDisplay(1240, 'job_count'), '1,240')
  assert.equal(weldingDisplay(1240, 'robot_no'), '1240')
  assert.equal(weldingDisplay('0012', 'welding_seam_no'), '0012')
  assert.equal(weldingDisplay(null, 'arc_rate'), '待核实')
})

test('welding column definitions provide bounded widths for every field', () => {
  for (const [kind, fields] of Object.entries(weldingColumns)) {
    for (const [key] of fields) {
      const layout = weldingColumnLayout[key]
      assert.ok(layout, `${kind}.${key}`)
      assert.ok(Number(layout.width ?? layout.minWidth) > 0, `${kind}.${key} must have a valid native width`)
    }
  }
  assert.equal(weldingColumnLayout.quality_reasons.width, 200)
  assert.equal(weldingColumnLayout.project_name.minWidth, 180)
})

test('welding paginated reports and part details use ten rows', async t => {
  for (const kind of ['daily', 'parts', 'jobs', 'equipment']) {
    for (const size of [undefined, '20', '50', 'bad']) {
      assert.equal(parseWeldingRouteQuery({ page_size: size }, kind, '2026-10-30').page_size, 10)
    }
  }
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => Response.json({ part: { id: 2 }, items: [], total: 0 }))
  await getWeldingPart(2, 3)
  const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost')
  assert.equal(url.searchParams.get('page'), '3')
  assert.equal(url.searchParams.get('page_size'), '10')
})

test('welding details render zero identifiers, missing measurements and escaped source values', async () => {
  const html = await renderComponent('components/WeldingRecord', { kind: 'jobs', row: {
    id: 7, device_no: 0, robot_no: 0, serial_number: '<script>source</script>',
    weld_length_m: null, working_hours: 0, state_label: '完成', quality_reasons: [],
  } })
  for (const text of ['作业基础信息', '工艺与作业产出', '完成', '&lt;script&gt;source&lt;/script&gt;', '0.000']) assert.ok(html.includes(text), text)
  assert.ok(!html.includes('<script>source</script>'))
  assert.match(html, /<dd[^>]*>0<\/dd>/)
  assert.ok(html.includes('未记录'))
  const empty = await renderComponent('components/WeldingTable', { kind: 'jobs', items: [], total: 0 })
  assert.ok(empty.includes('暂无数据'))
})

test('two welding instances use distinct cache entries and explicit request scopes', async (t) => {
  setDeviceCatalog({ default_device_id: 'welding', items: [
    { id: 'welding', name: '一号焊接', type: 'welding' },
    { id: 'weld-2', name: '二号焊接', type: 'welding' },
  ] })
  const scopes = []
  const original = globalThis.fetch
  globalThis.fetch = async (_url, options) => {
    scopes.push(options.headers['X-Production-Line'])
    return new Response(JSON.stringify({ success: true, data: { line: scopes.at(-1) } }), { headers: { 'Content-Type': 'application/json' } })
  }
  t.onTestFinished(() => { globalThis.fetch = original })
  const query = { start: '2026-10-01', end: '2026-10-02' }
  assert.equal((await getWeldingReport('daily', query)).line, 'welding')
  selectDevice('weld-2')
  assert.equal((await getWeldingReport('daily', query)).line, 'weld-2')
  selectDevice('welding')
  assert.equal((await getWeldingReport('daily', query)).line, 'welding')
  assert.deepEqual(scopes, ['welding', 'weld-2'])
})

const ok = data => Response.json({ success: true, data })

test('an explicit refresh drops its refresh flag after snapshot preparation and returns the completed data', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const urls = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async url => {
    urls.push(new URL(url, 'http://localhost'))
    return urls.length === 1 ? preparingResponse() : ok({ data_version: 'completed' })
  })
  const pending = getWeldingReport('daily', period, undefined, true)
  await settle()
  assert.equal(urls.length, 1)
  assert.equal(urls[0].searchParams.get('refresh'), 'true')
  await vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL)
  assert.equal((await pending).data_version, 'completed')
  assert.equal(urls.length, 2)
  assert.equal(urls[1].searchParams.has('refresh'), false)
  assert.equal(urls[1].searchParams.get('start'), period.start)
})

test('snapshot preparation stops after 75 checks instead of retrying forever', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => preparingResponse())
  const result = assert.rejects(getWeldingReport('dashboard', period), { code: 'snapshot_refresh_busy', status: 503 })
  await vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL * (REPORT_POLL_LIMIT + 1))
  await result
  assert.equal(fetchMock.mock.calls.length, 76)
  assert.equal(vi.getTimerCount(), 0)
})

test('aborting a preparation scope does not retry it or affect another filter', async () => {
  vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
  const requests = []
  vi.spyOn(globalThis, 'fetch').mockImplementation(async value => {
    const url = new URL(value, 'http://localhost')
    if (url.pathname.endsWith('/cancel')) return Response.json({})
    requests.push(url.searchParams.get('device_no'))
    return requests.at(-1) === '0' ? preparingResponse() : ok({ data_version: 'new-filter' })
  })
  const controller = new AbortController()
  const result = assert.rejects(getWeldingReport('jobs', { ...period, device_no: 0 }, controller.signal), { name: 'AbortError' })
  await settle()
  controller.abort()
  assert.equal((await getWeldingReport('jobs', { ...period, device_no: 1 })).data_version, 'new-filter')
  await result
  await vi.advanceTimersByTimeAsync(REPORT_POLL_INTERVAL)
  assert.deepEqual(requests, ['0', '1'])
})
