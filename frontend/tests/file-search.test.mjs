import { scriptFunction, runTypeScript } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { computed, reactive, ref } from 'vue'
import {
  getCuttingStatus,
  executeCuttingDispatch,
  createCuttingDispatchBatch,
  getCuttingDispatch,
  listCuttingCatalogs,
  listCuttingFiles,
  listCuttingProjects,
  listCuttingDispatches,
} from '../src/api/reports.ts'
import { reportCache } from '../src/api/reportCache.ts'
import { parseCuttingInstructionFilename } from '../src/utils/cuttingInstructionName.ts'
import { matchesDispatchParameters } from '../src/utils/cuttingDispatchBatch.ts'

const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  reportCache.invalidate('cutting:')
})

function dispatchLifecycle({ status, projects, listMachines, catalogs } = {}) {
  const source = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const calls = { status: [], projects: 0, history: 0 }
  const timers = new Map()
  let mounted, activated, timerId = 0
  const context = vm.createContext({
    ref, reactive, computed, AbortController,
    useRoute: () => ({ query: {} }), useRouter: () => ({ replace() {} }), useConfirm: () => async () => true,
    onMounted: fn => { mounted = fn }, onActivated: fn => { activated = fn }, onDeactivated() {}, onBeforeUnmount() {},
    getCuttingStatus: (_scope, options) => { calls.status.push(options); return status(options, calls.status.length) },
    listCuttingProjects: (...args) => { calls.projects += 1; return projects ? projects(...args) : Promise.resolve({ items: [{ project_id: 'P1', project_name: 'P1' }] }) },
    listCuttingCatalogs: catalogs || (async () => ({ tree: [] })),
    listCuttingMachines: listMachines || (async () => ({ items: [] })),
    listCuttingDispatches: async () => { calls.history += 1; return { items: [], total: 0 } },
    window: {
      setTimeout: (callback, delay) => { const id = ++timerId; timers.set(id, { callback, delay }); return id },
      clearTimeout: id => timers.delete(id),
    },
  })
  const script = source.match(/<script setup(?: lang="ts")?>([\s\S]*?)<\/script>/)[1].replace(/^import[\s\S]*?from ['"][^'"]+['"]\n/gm, '')
  runTypeScript(script + '\nthis.page = { initializePage, loadRuntimeStatus, loadMachines, pausePage, systemStatus, statusError, statusLoading, projectOptions, notice, machines };', context)
  return { ...context.page, calls, timers, context, mounted: () => mounted(), activated: () => activated() }
}

const flushDispatch = () => new Promise(resolve => setImmediate(resolve))

test('a slow readiness check cannot block the project directory or dispatch history after refresh', async () => {
  let completeStatus
  const page = dispatchLifecycle({ status: () => new Promise(resolve => { completeStatus = resolve }) })
  const initial = page.initializePage()
  await flushDispatch()
  try {
    assert.equal(page.projectOptions.value.length, 1, 'PDM browsing must not wait for the separate production SYS_CONFIG check')
    assert.equal(page.calls.history, 1)
  } finally {
    completeStatus({ can_push: true, magic_box: { configured: false } })
    await initial
  }
})

test('a timed-out readiness check offers a fresh retry while keeping the loaded project directory', async () => {
  const page = dispatchLifecycle({ status: ({ signal }, attempt) => attempt === 1
    ? new Promise((_resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true }))
    : Promise.resolve({ can_push: true, magic_box: { configured: false } }) })
  const initial = page.initializePage()
  await flushDispatch()
  const timer = [...page.timers.values()][0]
  assert.equal(timer.delay, 30_000)
  timer.callback()
  await initial
  assert.match(page.statusError.value, /超时/)
  assert.equal(page.statusLoading.value, false)
  assert.equal(page.systemStatus.value, null, 'readiness failure must not leave dispatch enabled')
  assert.equal(page.projectOptions.value.length, 1)
  await page.loadRuntimeStatus({ force: true })
  assert.equal(page.statusError.value, '')
  assert.equal(page.systemStatus.value.can_push, true)
  assert.equal(page.calls.status[1].force, true)
  assert.equal(page.calls.projects, 1, 'retrying readiness must not reset directory or file selection')
  assert.equal(page.timers.size, 0)
})

test('a failed readiness check does not present a false empty project list', async () => {
  const page = dispatchLifecycle({ status: async () => { throw new Error('SYS_CONFIG unavailable') } })
  await page.initializePage()
  assert.equal(page.projectOptions.value.length, 1)
  assert.equal(page.calls.history, 1)
  assert.match(page.statusError.value, /SYS_CONFIG unavailable/)
  assert.equal(page.systemStatus.value, null)
  assert.equal(page.timers.size, 0)
})

test('returning after an interrupted first load retries the directory and ignores late old responses', async () => {
  let oldStatus, oldProjects
  const page = dispatchLifecycle({
    status: (_options, attempt) => attempt === 1 ? new Promise(resolve => { oldStatus = resolve })
      : Promise.resolve({ can_push: true, magic_box: { configured: false } }),
    projects: () => page.calls.projects === 1 ? new Promise(resolve => { oldProjects = resolve })
      : Promise.resolve({ items: [{ project_id: 'new', project_name: 'new' }] }),
  })
  const initial = page.initializePage()
  await page.activated()
  page.pausePage()
  const resumed = page.activated()
  await flushDispatch()
  assert.equal(page.calls.projects, 2)
  assert.equal(page.projectOptions.value[0].project_id, 'new')
  oldStatus({ can_push: false })
  oldProjects({ items: [{ project_id: 'old', project_name: 'old' }] })
  await Promise.all([initial, resumed])
  assert.equal(page.systemStatus.value.can_push, true)
  assert.equal(page.projectOptions.value[0].project_id, 'new')
  assert.equal(page.statusError.value, '')
})

test('a completed empty project list is preserved on reactivation', async () => {
  const page = dispatchLifecycle({ status: async () => ({ can_push: false }), projects: async () => ({ items: [] }) })
  await page.initializePage()
  await page.activated()
  page.pausePage()
  await page.activated()
  assert.equal(page.calls.projects, 1)
  assert.equal(page.calls.status.length, 2)
})

test('cancelling machine status cannot write a stale fallback or error notice after leaving', async () => {
  let fail
  const page = dispatchLifecycle({ listMachines: () => new Promise((_resolve, reject) => { fail = reject }) })
  page.systemStatus.value = { magic_box: { configured: true, machine_ips: ['10.147.1.40'] } }
  const controller = new AbortController()
  const pending = page.loadMachines({ signal: controller.signal })
  controller.abort()
  fail(new DOMException('cancelled', 'AbortError'))
  await pending
  assert.equal(page.machines.value.length, 0)
  assert.equal(page.notice.value, '')
})

function historyPagination() {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const source = view.slice(view.indexOf('async function loadHistory('), view.indexOf('async function loadMachines('))
  const bindings = {
    pageActive: ref(true), histories: ref([]), historyPage: ref(1), historyPageSize: 10,
    historyTotal: ref(0), historyLoading: ref(false), historyError: ref(''),
    operatingId: ref(''), pushBatch: { saving: false }, listCuttingDispatches,
  }
  bindings.historyPageCount = computed(() => Math.max(1, Math.ceil(bindings.historyTotal.value / 10)))
  const functions = scriptFunction(...Object.keys(bindings), `let historyController = null; ${source}; return { loadHistory, changeHistoryPage }`)(...Object.values(bindings))
  return { ...bindings, ...functions }
}

test('dispatch history removes the ineffective cancel action while keeping safe execution retries', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const history = view.slice(view.indexOf('<section class="panel dispatch-history-panel"'), view.indexOf('<div v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar-spacer"'))

  assert.doesNotMatch(view, /取消指派|cancelSaved|cancelCuttingDispatch|can_cancel/)
  assert.match(history, /v-if="item.can_execute"[^>]*@click="executeSaved\(item\)"/)
  assert.match(history, /item.remote_task_id \? '重试指派' : '执行推送'/)
  assert.match(history, /<span v-if="!item.can_execute">--<\/span>/)
})

test('dispatch history pages through all records in ten-row pages independently of file selection', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    const query = new URL(String(url), 'http://localhost').searchParams
    const page = Number(query.get('page'))
    calls.push({ page, size: Number(query.get('page_size')) })
    const items = Array.from({ length: page === 3 ? 3 : 10 }, (_, index) => ({ dispatch_id: `${page}-${index}` }))
    return { ok: true, json: async () => ({ success: true, data: { items, total: 23, page } }) }
  }
  const history = historyPagination()
  await history.loadHistory()
  await history.changeHistoryPage(2)
  assert.equal(history.historyPage.value, 2)
  assert.equal(history.histories.value.length, 10)
  await history.changeHistoryPage(3)
  assert.equal(history.histories.value.length, 3)
  await history.changeHistoryPage(4)
  await history.loadHistory()
  assert.deepEqual(calls, [{ page: 1, size: 10 }, { page: 2, size: 10 }, { page: 3, size: 10 }, { page: 3, size: 10 }])
  assert.equal(history.historyPageCount.value, 3)
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /:items="histories"[^>]*:total="historyTotal"[^>]*:page="historyPage"[^>]*:page-size="historyPageSize"[^>]*@page="changeHistoryPage"/)
})

test('a failed history page keeps the displayed rows and page number instead of showing a false empty state', async () => {
  const history = historyPagination()
  history.histories.value = [{ dispatch_id: 'existing' }]
  history.historyTotal.value = 23
  globalThis.fetch = async () => { throw new Error('网络中断') }
  await history.changeHistoryPage(2)
  assert.equal(history.historyPage.value, 1)
  assert.equal(history.histories.value[0].dispatch_id, 'existing')
  assert.equal(history.historyTotal.value, 23)
  assert.equal(history.historyError.value, '网络中断')
  assert.equal(history.historyLoading.value, false)
})

test('history retention cannot strand the user on an empty out-of-range page', async () => {
  const pages = []
  globalThis.fetch = async (url) => {
    const page = Number(new URL(String(url), 'http://localhost').searchParams.get('page'))
    pages.push(page)
    return { ok: true, json: async () => ({ success: true, data: { items: page === 1 ? [{ dispatch_id: 'remaining' }] : [], total: 1, page } }) }
  }
  const history = historyPagination()
  history.historyPage.value = 3
  await history.loadHistory()
  assert.deepEqual(pages, [3, 1])
  assert.equal(history.historyPage.value, 1)
  assert.equal(history.histories.value[0].dispatch_id, 'remaining')
})

test('aborted history requests cannot overwrite the page after leaving the view', async () => {
  let resolveResponse
  let requestSignal
  globalThis.fetch = (url, options) => {
    if (String(url).includes('/requests/')) return Promise.resolve({ ok: true })
    requestSignal = options.signal
    return new Promise((resolve) => { resolveResponse = resolve })
  }
  const history = historyPagination()
  const controller = new AbortController()
  const pending = history.loadHistory({ signal: controller.signal })
  controller.abort()
  assert.equal(requestSignal.aborted, true)
  resolveResponse({ ok: true, json: async () => ({ success: true, data: { items: [{ dispatch_id: 'late' }], total: 1, page: 1 } }) })
  await pending
  assert.equal(history.histories.value.length, 0)
  assert.equal(history.historyError.value, '')
  assert.equal(history.historyLoading.value, false)
})

test('directory browser requests projects, one project tree, then one catalog page', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(String(url))
    return { ok: true, json: async () => ({ success: true, data: { items: [], tree: [], total: 0 } }) }
  }

  await listCuttingProjects('君正 25900')
  await listCuttingCatalogs('project/id')
  await listCuttingFiles({
    projectId: 'project/id',
    catalogId: 'catalog/id',
    keyword: 'PK-301',
    page: 2,
    pageSize: 20,
  })

  assert.equal(calls.length, 3)
  assert.match(calls[0], /^\/api\/v1\/cutting\/projects\?/)
  assert.match(calls[0], /keyword=%E5%90%9B%E6%AD%A3\+25900/)
  assert.match(calls[0], /limit=1000/)
  assert.match(calls[1], /project_id=project%2Fid/)
  assert.match(calls[2], /catalog_id=catalog%2Fid/)
  assert.match(calls[2], /keyword=PK-301/)
  assert.match(calls[2], /page=2/)
  assert.match(calls[2], /page_size=20/)
})

test('duplicate project and catalog requests share a five-minute cache', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(String(url))
    return { ok: true, json: async () => ({ success: true, data: { items: [], tree: [] } }) }
  }

  await Promise.all([listCuttingProjects(), listCuttingProjects()])
  await Promise.all([listCuttingCatalogs('P-CACHED'), listCuttingCatalogs('P-CACHED')])
  const fileQuery = {
    projectId: 'P-CACHED',
    catalogId: 'C-CACHED',
    page: 1,
    pageSize: 20,
  }
  await Promise.all([listCuttingFiles(fileQuery), listCuttingFiles(fileQuery)])
  await listCuttingProjects()
  await listCuttingCatalogs('P-CACHED')
  await listCuttingFiles(fileQuery)

  assert.equal(calls.length, 3)
})

test('unified dispatch page loads push readiness status', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(String(url))
    return { ok: true, json: async () => ({ success: true, data: {} }) }
  }

  await getCuttingStatus('push')

  assert.equal(calls.length, 1)
  assert.match(calls[0], /^\/api\/v1\/cutting\/status\?scope=push$/)
})

test('an explicit readiness retry bypasses a cached unconfigured status', async () => {
  let requests = 0
  globalThis.fetch = async () => ({ ok: true, json: async () => ({ success: true, data: { can_push: ++requests > 1 } }) })
  assert.equal((await getCuttingStatus('push')).can_push, false)
  assert.equal((await getCuttingStatus('push')).can_push, false)
  assert.equal((await getCuttingStatus('push', { force: true })).can_push, true)
  assert.equal(requests, 2)
})

test('machine selectors show Chinese names and IPs while dispatch still uses their IPs', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const source = view.match(/function machineName\(machineIp: string\) \{[\s\S]*?\n\}/)[0]
  const machines = { value: [{ machine_ip: '10.147.1.41', server_name: '其他机床' }] }
  const machineName = scriptFunction('machines', `${source}\nreturn machineName`)(machines)

  assert.equal(machineName('10.147.1.40'), '20kW 切割机')
  assert.equal(machineName('10.147.1.38'), '30kW 切割机')
  assert.equal(machineName('10.147.1.41'), '其他机床')
  assert.equal(machineName('10.147.1.42'), '未命名机床')
  assert.equal((view.match(/:value="machine.machine_ip"/g) || []).length, 2)
  assert.equal((view.match(/\{\{ machineName\(machine.machine_ip\) \}\}/g) || []).length, 2)
  assert.match(view, /machine_ip: dispatchForm.machineIp/)
  assert.match(view, /#c2="[^"]*">\{\{ machineName\(item.machine_ip\) \}\}<\/template>/)
  assert.equal((view.match(/\{\{ machineName\(machine.machine_ip\) \}\} · \{\{ machine.machine_ip \}\}（/g) || []).length, 2)
  assert.doesNotMatch(view, /目标机床 IP|<th>机床IP<\/th>/)
})

test('file dispatch page uses the recursive PDM directory tree without condition builders', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const node = readFileSync(new URL('../src/components/CatalogTreeNode.vue', import.meta.url), 'utf8')

  assert.match(view, /<CatalogTreeNode/)
  assert.match(view, /listCuttingProjects/)
  assert.match(view, /listCuttingCatalogs/)
  assert.match(view, /listCuttingFiles/)
  assert.match(view, /getCuttingStatus\('push', \{ signal: controller\.signal, force \}\)/)
  assert.doesNotMatch(view, /defineProps|isDownload/)
  assert.match(view, /项目目录/)
  assert.match(view, /当前位置/)
  assert.match(view, /const pageSize = 20/)
  assert.match(view, /const projectOptions = ref<CuttingProject\[\]>\(\[\]\)/)
  assert.match(view, /const selectedProjectId = ref\(initialProjectId\)/)
  assert.match(view, /<NAutoComplete[^\n]*v-model:value="projectSearch"[^\n]*@select="openSelectedProject"/)
  assert.match(view, /:options="projectSuggestions"/)
  assert.match(view, /:get-show="\(\) => true"/)
  assert.doesNotMatch(view, /project-keyword|projectKeyword|filteredProjectOptions|applyProjectKeyword|resetProjectSelection|project-select-row/)
  assert.match(view, /支持项目/)
  assert.match(view, /项目名称或船号/)
  assert.doesNotMatch(view, /<option value="" disabled>\{\{ projectLoading/)
  assert.match(view, /\.project-search \.project-selector \{ height:36px;[^}]*font-size:12px;/)
  assert.match(view, /:render-label="option => \(option as \{ displayLabel: string \}\).displayLabel"/)
  assert.match(view, /title: "文件名"[\s\S]*title: "命名检查"[\s\S]*title: "中间产品"[\s\S]*title: "文件格式"[\s\S]*title: "版本"[\s\S]*title: "操作人"[\s\S]*title: "操作人工号"[\s\S]*title: "操作时间"[\s\S]*title: "派发状态"[\s\S]*title: "操作"/)
  assert.doesNotMatch(view, /<th>切割状态<\/th>/)
  assert.match(view, /fileExtension\(row\)/)
  assert.doesNotMatch(view, /ConditionBuilder|SortBuilder|searchCuttingFiles|组合条件查询/)
  assert.match(node, /<CatalogTreeNode/)
  assert.match(node, /node\.children/)
})

test('confirming a project opens it immediately, preserves same-project selection and ignores superseded catalogs', async () => {
  const requests = []
  let finishOld
  const page = dispatchLifecycle({ catalogs: (id, { signal }) => {
    requests.push({ id, signal })
    return id === 'P2' ? new Promise(resolve => { finishOld = resolve }) : Promise.resolve({ tree: [{ catalog_id: `C-${id}`, title: id }] })
  } })
  const state = runTypeScript('({ projectOptions, selectedProjectId, selectedProject, selectedCatalog, selectedFiles, rows, catalogTree, openSelectedProject })', page.context)
  state.projectOptions.value = ['P1', 'P2', 'P3'].map(id => ({ project_id: id, project_name: id }))
  state.selectedProjectId.value = 'P1'
  await state.openSelectedProject()
  state.selectedCatalog.value = { catalog_id: 'C-P1' }
  state.selectedFiles.value.set('file-1', { document_id: 'file-1' })
  state.rows.value = [{ document_id: 'file-1' }]
  await state.openSelectedProject()
  assert.equal(requests.length, 1)
  assert.equal(state.selectedFiles.value.size, 1)
  state.selectedProjectId.value = 'P2'
  const pending = state.openSelectedProject()
  assert.equal(state.selectedFiles.value.size, 0)
  assert.equal(state.selectedCatalog.value, null)
  assert.equal(state.rows.value.length, 0)
  state.selectedProjectId.value = 'P3'
  await state.openSelectedProject()
  assert.equal(requests[1].signal.aborted, true)
  finishOld({ tree: [{ catalog_id: 'stale', title: 'old' }] })
  await pending
  assert.equal(state.selectedProject.value.project_id, 'P3')
  assert.equal(state.catalogTree.value[0].catalog_id, 'C-P3')
})

test('selected dispatch project survives refresh without a saved-file browser', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /useRoute, useRouter/)
  assert.match(view, /route\.query\.project_id/)
  assert.match(view, /router\.replace\(\{ query \}\)/)
  assert.match(view, /if \(projectToOpen && pageActive\.value && !signal\.aborted && sequence === querySequence\) await selectProject\(projectToOpen\)/)
  assert.doesNotMatch(view, /CuttingDownloadsPanel|downloadsPanel|selectProjectById/)
})

test('project-shaped catalog root is hidden to avoid showing the project twice', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /roots\.length === 1/)
  assert.match(view, /project\.project_name/)
  assert.match(view, /roots = roots\[0\]\.children/)
})

test('file dispatch status is displayed after operation time with the history badge and empty state', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /title: "操作时间"[\s\S]*title: "派发状态"[\s\S]*title: "操作"/)
  assert.match(view, /class="dispatch-status" :class="row.dispatch_status.status"/)
  assert.match(view, /row.dispatch_status.status_label/)
  assert.match(view, /row.dispatch_status.stage_label/)
  assert.match(view, /v-else class="dispatch-project-name">暂无记录/)
  assert.match(view, /#empty>[\s\S]*当前目录没有匹配的文件/)
  assert.match(view, /Promise.allSettled\(\[loadHistory\(\), refreshVisibleFileStatuses\(\)\]\)/)
})

test('file status refresh preserves selections and ignores responses after a directory switch', async () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const source = view.slice(view.indexOf('async function refreshVisibleFileStatuses('), view.indexOf('async function refreshBatchHistory('))
  let complete
  const calls = []
  const file = { document_id: 'doc', version_id: 'v1', normalized_path: 'file.gen' }
  const bindings = {
    selectedProject: ref({ project_id: 'P1' }), selectedCatalog: ref({ catalog_id: 'C1' }),
    pageActive: ref(true), appliedFileKeyword: ref('307C'), page: ref(3), pageSize: 20,
    rows: ref([file]), fileKey: row => JSON.stringify([row.document_id, row.version_id, row.normalized_path]),
    listCuttingFiles: (query, options) => { calls.push({ query, options }); return new Promise(resolve => { complete = resolve }) },
  }
  const methods = scriptFunction(...Object.keys(bindings), `let querySequence = 0; ${source}; return { refreshVisibleFileStatuses, switchDirectory: () => { querySequence += 1 } }`)(...Object.values(bindings))
  const pending = methods.refreshVisibleFileStatuses()
  const status = { status: 'sent', status_label: '已派发', stage_label: '推送完成' }
  complete({ items: [{ ...file, dispatch_status: status }] })
  await pending
  assert.deepEqual(bindings.rows.value[0].dispatch_status, status)
  assert.deepEqual(calls[0], { query: { projectId: 'P1', catalogId: 'C1', keyword: '307C', page: 3, pageSize: 20 }, options: { force: true } })
  const late = methods.refreshVisibleFileStatuses()
  methods.switchDirectory()
  complete({ items: [{ ...file, dispatch_status: null }] })
  await late
  assert.deepEqual(bindings.rows.value[0].dispatch_status, status)
})

test('dispatch mutations invalidate cached file status even when execution fails', async () => {
  let reads = 0
  globalThis.fetch = async (url, options) => {
    if (options.method === 'POST') {
      if (String(url).endsWith('/execute')) throw new Error('推送失败')
      return { ok: true, json: async () => ({ success: true, data: {} }) }
    }
    reads += 1
    return { ok: true, json: async () => ({ success: true, data: { items: [], total: reads } }) }
  }
  const query = { projectId: 'P1', catalogId: 'C1' }
  await listCuttingFiles(query)
  await listCuttingFiles(query)
  assert.equal(reads, 1)
  await createCuttingDispatchBatch({ items: [] })
  await listCuttingFiles(query)
  assert.equal(reads, 2)
  await assert.rejects(executeCuttingDispatch('task1'), /推送失败/)
  await listCuttingFiles(query)
  assert.equal(reads, 3)
  await listCuttingFiles(query, { force: true })
  assert.equal(reads, 4)
})

function fileSelection(files = [], { total = files.length, listFiles } = {}) {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const states = view.slice(view.indexOf('const selectableRows ='), view.indexOf('const selectedPath ='))
  const handlers = view.slice(view.indexOf('function fileKey('), view.indexOf('function openSinglePush('))
  const notices = []
  const bindings = {
    computed, rows: ref(files), total: ref(total), selectedFiles: ref(new Map()),
    catalogSelectionRows: ref(null), selectingAll: ref(false), loading: ref(false),
    pushBatch: { saving: false }, pageActive: ref(true),
    selectedProject: ref({ project_id: 'P1' }), selectedCatalog: ref({ catalog_id: 'C1' }),
    appliedFileKeyword: ref(''), listCuttingFiles: listFiles,
    systemStatus: ref({ can_push: true }), showNotice: text => notices.push(text),
  }
  const selection = scriptFunction(...Object.keys(bindings), `let querySequence = 0, directoryController;
    function beginDirectoryRequest() {
      directoryController?.abort(); directoryController = new AbortController(); return directoryController.signal
    }
    ${states}\n${handlers}\nreturn { fileKey, setFileSelected, clearSelectedFiles, toggleCatalogFiles, allCatalogFilesSelected, someCatalogFilesSelected }`)(...Object.values(bindings))
  return { ...bindings, ...selection, notices }
}

const selectableFile = (id, documentNo = 'S806C080A001.gen') => ({ document_id: String(id), version_id: 'v1', normalized_path: `files/${id}.gen`, document_no: documentNo, path_available: true })
const checkboxEvent = checked => checked

test('select-all includes nonstandard names and skips unavailable paths; clearing affects the loaded directory scope', async () => {
  const files = [selectableFile(1), selectableFile(2, 'custom-cutting.gen'), { ...selectableFile(3), path_available: false }]
  const selection = fileSelection(files)
  const otherPage = selectableFile(4)
  selection.selectedFiles.value.set(selection.fileKey(otherPage), otherPage)
  let event = checkboxEvent(true)
  await selection.toggleCatalogFiles(event)
  assert.equal(selection.selectedFiles.value.size, 3)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(files[1])), true)
  assert.equal(selection.allCatalogFilesSelected.value, true)
  assert.equal(selection.someCatalogFilesSelected.value, false)
  event = false
  await selection.toggleCatalogFiles(event)
  assert.equal(selection.selectedFiles.value.size, 1)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(otherPage)), true)
})

test('a full selection on another page cannot leave a false checked header or row', async () => {
  const selection = fileSelection([selectableFile(101, 'custom-cutting.gen')])
  for (let i = 1; i <= 100; i += 1) selection.setFileSelected(selectableFile(i), checkboxEvent(true))
  assert.deepEqual(selection.notices, [], 'selecting exactly 100 files must not show a limit warning')
  let header = checkboxEvent(true)
  await selection.toggleCatalogFiles(header)
  assert.equal(selection.allCatalogFilesSelected.value, false)
  assert.equal(selection.someCatalogFilesSelected.value, true)
  const row = checkboxEvent(true)
  selection.setFileSelected(selection.rows.value[0], row)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(selection.rows.value[0])), false)
  assert.equal(selection.selectedFiles.value.size, 100)
  assert.match(selection.notices.at(-1), /已达 100 项上限/)
})

test('select-all accepts exactly 100 files quietly and warns only for an extra file', async () => {
  const selection = fileSelection(Array.from({ length: 100 }, (_, i) => selectableFile(i)))
  let header = checkboxEvent(true)
  await selection.toggleCatalogFiles(header)
  assert.equal(selection.selectedFiles.value.size, 100)
  assert.equal(selection.allCatalogFilesSelected.value, true)
  assert.deepEqual(selection.notices, [])
  const extra = checkboxEvent(true)
  selection.setFileSelected(selectableFile(101), extra)
  assert.equal(selection.selectedFiles.value.size, 100)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(selectableFile(101))), false)
  assert.match(selection.notices.at(-1), /已达 100 项上限/)
})

test('select-all shows partial state when only remaining capacity fits and preserves off-page selections', async () => {
  const selection = fileSelection([selectableFile(101), selectableFile(102, 'custom-cutting.gen'), selectableFile(103)])
  for (let i = 1; i <= 98; i += 1) selection.setFileSelected(selectableFile(i), checkboxEvent(true))
  let header = checkboxEvent(true)
  await selection.toggleCatalogFiles(header)
  assert.equal(selection.selectedFiles.value.size, 100)
  assert.equal(selection.allCatalogFilesSelected.value, false)
  assert.equal(selection.someCatalogFilesSelected.value, true)
  header = false
  await selection.toggleCatalogFiles(header)
  assert.equal(selection.selectedFiles.value.size, 98)
  assert.equal(selection.allCatalogFilesSelected.value, false)
  assert.equal(selection.someCatalogFilesSelected.value, true)
})

test('duplicate versions cannot leave false checkbox state or replace a previously selected version', async () => {
  const original = selectableFile(1)
  const changedVersion = { ...original, version_id: 'v2', normalized_path: 'files/1-v2.gen' }
  const selection = fileSelection([changedVersion])
  selection.setFileSelected(original, checkboxEvent(true))
  let header = checkboxEvent(true)
  await selection.toggleCatalogFiles(header)
  assert.equal(selection.allCatalogFilesSelected.value, false)
  const row = checkboxEvent(true)
  selection.setFileSelected(changedVersion, row)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(selection.rows.value[0])), false)
  assert.equal(selection.selectedFiles.value.size, 1)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(original)), true)
  assert.match(selection.notices.at(-1), /只能选择一个版本/)
})

test('directory select-all loads every page with the applied filter and stays selected after paging', async () => {
  const files = Array.from({ length: 42 }, (_, i) => selectableFile(i))
  const calls = []
  const selection = fileSelection(files.slice(40), { total: 42, listFiles: async (query) => {
    calls.push(query)
    return { items: files, total: 42 }
  } })
  selection.appliedFileKeyword.value = '307C'
  let header = checkboxEvent(true)
  await selection.toggleCatalogFiles(header)
  assert.equal(selection.selectedFiles.value.size, 42)
  assert.equal(selection.allCatalogFilesSelected.value, true)
  assert.deepEqual(calls, [{ projectId: 'P1', catalogId: 'C1', keyword: '307C', page: 1, pageSize: 100 }])
  selection.rows.value = files.slice(0, 20)
  assert.equal(selection.allCatalogFilesSelected.value, true)
  selection.setFileSelected(files[41], checkboxEvent(false))
  assert.equal(selection.allCatalogFilesSelected.value, false)
  assert.equal(selection.someCatalogFilesSelected.value, true)
  await selection.toggleCatalogFiles(checkboxEvent(true))
  assert.equal(selection.selectedFiles.value.size, 42)
  await selection.toggleCatalogFiles(checkboxEvent(false))
  assert.equal(selection.selectedFiles.value.size, 0)
  assert.equal(calls.length, 1)
})

test('directory select-all fetches beyond 100 rows, skips unavailable paths and duplicate versions, and reports the limit', async () => {
  const files = Array.from({ length: 103 }, (_, i) => selectableFile(i))
  files[1] = { ...files[0], version_id: 'v2' }
  files[2].path_available = false
  const pages = []
  const selection = fileSelection(files.slice(0, 20), { total: files.length, listFiles: async ({ page, pageSize }) => {
    pages.push(page)
    return { items: files.slice((page - 1) * pageSize, page * pageSize), total: files.length }
  } })
  let header = checkboxEvent(true)
  await selection.toggleCatalogFiles(header)
  assert.deepEqual(pages, [1, 2])
  assert.equal(selection.selectedFiles.value.size, 100)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(files[1])), false)
  assert.equal(selection.selectedFiles.value.has(selection.fileKey(files[2])), false)
  assert.equal(selection.allCatalogFilesSelected.value, false)
  assert.equal(selection.someCatalogFilesSelected.value, true)
  assert.match(selection.notices.at(-1), /另有 2 项/)
})

test('failed directory select-all preserves previous selections without committing partial pages', async () => {
  const files = Array.from({ length: 101 }, (_, i) => selectableFile(i))
  const selection = fileSelection(files.slice(0, 20), { total: files.length, listFiles: async ({ page }) => {
    if (page === 2) throw new Error('网络中断')
    return { items: files.slice(0, 100), total: files.length }
  } })
  selection.setFileSelected(files[0], checkboxEvent(true))
  let header = checkboxEvent(true)
  const pending = selection.toggleCatalogFiles(header)
  assert.equal(selection.loading.value, true)
  await pending
  assert.equal(selection.loading.value, false)
  assert.equal(selection.selectedFiles.value.size, 1)
  assert.equal(selection.allCatalogFilesSelected.value, false)
  assert.equal(selection.someCatalogFilesSelected.value, true)
  assert.match(selection.notices.at(-1), /全选失败.*网络中断/)
})

test('clearing or switching directory during select-all discards late results and resets the scope', async () => {
  let complete, signal
  const files = Array.from({ length: 42 }, (_, i) => selectableFile(i))
  const selection = fileSelection(files.slice(0, 20), { total: 42, listFiles: (_query, options) => {
    signal = options.signal
    return new Promise(resolve => { complete = resolve })
  } })
  const pending = selection.toggleCatalogFiles(checkboxEvent(true))
  selection.clearSelectedFiles()
  selection.selectedCatalog.value = { catalog_id: 'C2' }
  complete({ items: files, total: 42 })
  await pending
  assert.equal(signal.aborted, true)
  assert.equal(selection.selectedFiles.value.size, 0)
  assert.equal(selection.catalogSelectionRows.value, null)
  assert.equal(selection.loading.value, false)
})

test('empty and unavailable-only pages do not falsely indicate full directory selection', async () => {
  const empty = fileSelection([])
  let header = checkboxEvent(true)
  await empty.toggleCatalogFiles(header)
  assert.equal(empty.allCatalogFilesSelected.value, false)
  assert.equal(empty.someCatalogFilesSelected.value, false)
  const available = selectableFile(21)
  const selection = fileSelection([{ ...selectableFile(1), path_available: false }], {
    total: 21, listFiles: async () => ({ items: [available], total: 1 }),
  })
  await selection.toggleCatalogFiles(checkboxEvent(true))
  assert.equal(selection.selectedFiles.value.size, 1)
  assert.equal(selection.allCatalogFilesSelected.value, true)
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /:disabled="!total \|\| !systemStatus\?\.can_push \|\| loading \|\| pushBatch\.saving"/)
  assert.match(view, /正在选择所有分页的文件/)
})

test('PDM file table supports up to one hundred selections and direct batch dispatch', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /const selectedFiles = ref\(new Map<string, CuttingFile>\(\)\)/)
  assert.match(view, /function toggleCatalogFiles/)
  assert.match(view, /next\.size >= 100/)
  assert.match(view, /aria-label="全选当前目录所有分页的可派发文件"/)
  assert.match(view, /@update:checked="toggleCatalogFiles\(\$event\)"/)
  assert.match(view, /@update:checked="setFileSelected\(row, \$event\)"/)
  assert.doesNotMatch(view, /selectedFileCount >= 100" role="status"/)
  assert.match(view, /:aria-label="`选择文件 \$\{row\.document_no\}`"/)
  assert.match(view, /设置参数并派发/)
  assert.match(view, /@click="openSinglePush\(row\)"/)
  assert.match(view, /:columns="fileTableColumns"/)
  assert.doesNotMatch(view, /downloadCuttingFile|保存到系统|listCuttingDownloads/)
  assert.match(view, /<AppReportTable\b[^>]*:columns="fileTableColumns"/)
})

test('mobile file selection keeps batch actions reachable at the bottom of the viewport', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /v-if="selectedFileCount && !pushBatch\.visible" class="mobile-selection-bar"/)
  assert.match(view, /class="mobile-selection-bar" aria-label="已选文件操作"/)
  assert.match(view, /class="mobile-selection-bar"[\s\S]*@click="clearSelectedFiles"[\s\S]*@click="openPushBatch"/)
  assert.match(view, /\.mobile-selection-bar,\.mobile-selection-bar-spacer \{ display:none; \}/)
  assert.match(view, /@media \(max-width:680px\) \{[\s\S]*?\.mobile-selection-bar \{ position:fixed;[^}]*bottom:0;[^}]*display:grid;/)
  assert.match(view, /\.mobile-selection-bar button \{ min-height:44px;/)
  assert.match(view, /env\(safe-area-inset-bottom\)/)
})

test('file dispatch presents the four-step dashboard and persistent confirmation summary', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /CUTTING FILE DISPATCH/)
  assert.match(view, /选择项目目录/)
  assert.match(view, /选择文件/)
  assert.match(view, /确认派发/)
  assert.match(view, /完成派发/)
  assert.match(view, /class="panel dispatch-confirm-panel"/)
  assert.match(view, /const selectedFileItems = computed/)
  assert.match(view, /const activeDispatchStep = computed/)
  assert.match(view, /v-for="file in selectedFileItems\.slice\(0, 5\)"/)
  assert.match(view, /<AppIcon name="send"/)
  assert.match(view, /\.dispatch-workspace\.download-workspace \{ grid-template-columns:280px minmax\(0,1fr\)/)
  assert.match(view, /\.dispatch-confirm-panel \{[^}]*grid-column:1\/-1;[^}]*align-self:start;/)
  assert.match(view, /\.directory-panel \{[^}]*display:grid;[^}]*grid-template-rows:auto auto minmax\(0,1fr\);/)
  assert.match(view, /\.directory-panel \{[^}]*grid-template-columns:minmax\(0,1fr\);/)
  assert.match(view, /\.project-search \.project-selector,\.file-name-search input \{ width:100%; min-width:0;/)
  assert.match(view, /\.download-workspace \.dispatch-files-panel \{[^}]*min-height:600px;/)
  assert.match(view, /\.directory-scroll \{[^}]*grid-area:3 \/ 1 \/ 4 \/ -1;[^}]*position:absolute;[^}]*overflow:auto;/)
  assert.match(view, /@media \(max-width:1000px\) \{[\s\S]*?\.directory-panel \{ display:flex; flex-direction:column; \}/)
  assert.match(view, /@media \(max-width:1000px\) \{[\s\S]*?\.download-workspace \.dispatch-files-panel \{ min-height:0; \}/)
  assert.match(view, /\.directory-scroll \{ position:static; min-height:220px; max-height:410px; flex:0 1 auto; \}/)
  assert.match(view, /\.source-file-table \{ min-width:820px; \}/)
})

test('standard cutting instruction names provide material and thickness for dispatch', () => {
  assert.deepEqual(parseCuttingInstructionFilename('P101C100A001.GEN'), {
    instructionType: 'P',
    instructionTypeLabel: '拼板',
    intermediateProduct: '101C',
    thickness: 10,
    materialCode: 'A0',
    material: 'A0',
    sequence: '01',
  })
  assert.equal(parseCuttingInstructionFilename('s201c090a204.txt').material, 'a2')
  assert.equal(parseCuttingInstructionFilename('W102C125A606.GEN').material, 'A6')
  assert.equal(parseCuttingInstructionFilename('L301C080SS12.GEN').material, 'SS')
  assert.equal(parseCuttingInstructionFilename('L301C080SS12.GEN').thickness, 8)
})

test('confirmed Z-prefixed files use standard material and thickness without manual input', () => {
  for (let sequence = 1; sequence <= 5; sequence += 1) {
    const filename = `Z806C080A0${String(sequence).padStart(2, '0')}.gen`
    assert.deepEqual(parseCuttingInstructionFilename(filename), {
      instructionType: 'Z', instructionTypeLabel: 'Z', intermediateProduct: '806C',
      thickness: 8, materialCode: 'A0', material: 'A0', sequence: String(sequence).padStart(2, '0'),
    })
    assert.equal(parseCuttingInstructionFilename(filename.toLowerCase()).thickness, 8)
  }
})

test('non-standard cutting instruction names are ignored', () => {
  for (const filename of [
    'PK-301CPS1.GEN',
    'prefix-P101C100A001.GEN',
    'P101C000A001.GEN',
    'P403P08A601.GEN',
    'P403P080.GEN',
    'P101C100A.GEN',
    'P101C100A001.PDF',
    'Z806C000A001.gen',
    'Z806C080B.gen',
    'Z806C080A001.pdf',
    'X806C080A001.gen',
  ]) {
    assert.equal(parseCuttingInstructionFilename(filename), null)
  }
})

test('dispatch parameters depend on material and thickness, not the product code or suffix', () => {
  for (const filename of ['P403P080A601', 'P403P080A601B.gen', 'P403P080A6', 'P403P080A600.gen', 'P12X080A601AB.txt', 'folder\\P403P080A601C.GEN']) {
    const parsed = parseCuttingInstructionFilename(filename)
    assert.equal(parsed.material, 'A6', filename)
    assert.equal(parsed.thickness, 8, filename)
  }
  const parsed = parseCuttingInstructionFilename('p403p135a001b.gen')
  assert.equal(parsed.material, 'a0')
  assert.equal(parsed.thickness, 13.5)
  assert.equal(parseCuttingInstructionFilename('P403P080A601B135A001.gen').thickness, 8, 'the first parameter prefix wins')
})

test('any two-character material code is filled verbatim without a grade mapping or manual override', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const createSource = view.slice(view.indexOf('function createPushBatchItem('), view.indexOf('function batchItemError('))
  const mismatchSource = view.slice(view.indexOf('function hasNameMismatch('), view.indexOf('function openPushBatch('))
  const { createPushBatchItem, hasNameMismatch } = scriptFunction('parseCuttingInstructionFilename', 'fileKey',
    `${createSource}\n${mismatchSource}\nreturn {createPushBatchItem, hasNameMismatch}`)(parseCuttingInstructionFilename, file => file.document_id)
  for (const code of ['A0', 'A2', 'A6', 'SS', 'B0', 'A7', 'X9', '7X', '00', 'q7', '@0', '中钢', '🧪0']) {
    const filename = `S803C320${code}01L.gen`
    const parsed = parseCuttingInstructionFilename(filename)
    assert.equal(parsed.materialCode, code)
    assert.equal(parsed.material, code)
    assert.equal(parsed.thickness, 32)
    const item = createPushBatchItem({ document_no: filename, document_id: code, normalized_path: filename })
    assert.equal(item.material, code)
    assert.equal(hasNameMismatch(item), false)
    assert.equal(hasNameMismatch({ ...item, material: `${code}-changed` }), true)
  }
  assert.equal(parseCuttingInstructionFilename('S306C095A602L.gen').material, 'A6')
})

test('direct dispatch builds independent PDM references and executes tasks sequentially', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /next\.size >= 100/)
  assert.match(view, /function createPushBatchItem\(file: CuttingFile\)/)
  assert.match(view, /const parsed = parseCuttingInstructionFilename\(filename\)/)
  assert.match(view, /material: parsed\?\.material \|\| ''/)
  assert.match(view, /thickness: parsed\?\.thickness \?\? ''/)
  assert.match(view, /quantity: 1/)
  assert.match(view, /createCuttingDispatchBatch/)
  assert.match(view, /document_id: item\.documentId/)
  assert.match(view, /version_id: item\.versionId/)
  assert.match(view, /file_path: item\.filePath/)
  assert.match(view, /for \(const item of createdItems\)/)
  assert.match(view, /await executeCuttingDispatch\(item\.dispatchId, item\.segmentName\)/)
  assert.match(view, /非标准名，请手工填写参数/)
})

test('non-standard names require explicit manual confirmation before dispatch', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /const nonStandardBatchCount = computed/)
  assert.match(view, /pushBatch\.nonStandardConfirmed/)
  assert.match(view, /我已人工核对 \{\{ nonStandardBatchCount \}\} 个非标准文件名/)
  assert.match(view, /nonStandardBatchCount > 0 && !pushBatch\.nonStandardConfirmed/)
  assert.match(view, /completed\.local_file_cleanup\?\.error/)
  assert.doesNotMatch(view, /仅保存任务|批量保存|再次保存/)
})

test('batch confirmation checks standard-name overrides and blocks duplicate versions', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /alreadySelectedDocument\(next, file\)/)
  assert.match(view, /同一 PDM 文件只能选择一个版本/)
  assert.match(view, /hasNameMismatch\(item\)/)
  assert.match(view, /name_check_confirmed:/)
  assert.match(view, /standard_override_confirmed/)
  assert.match(view, /@update:value="invalidateBatchConfirmation\(item\)"/)
  assert.match(view, /pushBatch\.nonStandardConfirmed = false/)
  assert.match(view, /pushBatch\.overrideConfirmed = false/)
  assert.match(view, /overrideBatchCount > 0 && !pushBatch\.overrideConfirmed/)
  assert.match(view, /for \(const \[index, item\] of pushBatch\.items\.entries\(\)\)/)
})

test('retry only continues a matching dispatch after reading its current status', async () => {
  const current = {
    document_id: 'doc-1', version_id: 'v2', relative_path: 'project/file.GEN',
    machine_ip: '10.1.133.197', material: 'AH36', thickness: 12, quantity: 2,
  }
  const selected = {
    documentId: 'doc-1', versionId: 'v2', filePath: 'project\\file.GEN',
    material: 'AH36', thickness: 12, quantity: 2,
  }
  assert.equal(matchesDispatchParameters(current, selected, '10.1.133.197'), true)
  for (const changed of [
    { version_id: 'v3' }, { relative_path: 'project/other.GEN' },
    { machine_ip: '10.1.133.198' }, { material: 'AH32' },
    { thickness: 10 }, { quantity: 3 },
  ]) {
    assert.equal(matchesDispatchParameters({ ...current, ...changed }, selected, '10.1.133.197'), false)
  }

  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(String(url))
    return { ok: true, json: async () => ({ success: true, data: { ...current, can_execute: false, stage: 'upload_unknown' } }) }
  }
  const status = await getCuttingDispatch('task/1')
  assert.equal(status.can_execute, false)
  assert.deepEqual(calls, ['/api/v1/cutting/dispatches/task%2F1'])

  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /const current = await getCuttingDispatch\(item\.dispatchId\)/)
  assert.match(view, /if \(current\.status === 'sent' \|\| !item\.retryable\) return/)
  assert.match(view, /上传结果待核对的任务须先检查魔盒/)
  assert.match(view, /核对并继续已有任务/)
  assert.match(view, /刷新清单重选/)
})

test('project suggestions preserve the typed search while selecting and switching projects', async () => {
  const page = dispatchLifecycle()
  const state = runTypeScript('({ projectOptions, projectSearch, projectSuggestions, selectedProject, openSelectedProject })', page.context)
  state.projectOptions.value = [
    { project_id: 'P1', project_name: '扬州切割联调项目', ship_no: 'CMYZ0177' },
    { project_id: 'P2', project_name: '文件派发模拟项目', ship_no: 'CMYZ0188' },
  ]
  state.projectSearch.value = '  cmyz0177  '
  assert.equal(state.projectSuggestions.value.length, 1)
  const option = state.projectSuggestions.value[0]
  assert.equal(option.displayLabel, '扬州切割联调项目 · CMYZ0177')
  assert.equal(Object.hasOwn(option, 'label'), false, 'autocomplete must not replace the typed query on selection')
  await state.openSelectedProject(option.value)
  assert.equal(state.projectSearch.value, '  cmyz0177  ')
  assert.equal(state.selectedProject.value.project_id, 'P1')
  state.projectSearch.value = '模拟'
  await state.openSelectedProject(state.projectSuggestions.value[0].value)
  assert.equal(state.projectSearch.value, '模拟')
  assert.equal(state.selectedProject.value.project_id, 'P2')
  state.projectSearch.value = '不存在'
  assert.equal(state.projectSuggestions.value.length, 0)
  state.projectSearch.value = null
  assert.equal(state.projectSuggestions.value.length, 2)
})

test('dispatch segments are recognized or required before a nonstandard file can be pushed', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const source = view.slice(view.indexOf('function createPushBatchItem('), view.indexOf('function hasNameMismatch('))
  const { createPushBatchItem, batchItemError } = scriptFunction('parseCuttingInstructionFilename', 'fileKey', `${source}\nreturn { createPushBatchItem, batchItemError }`)(parseCuttingInstructionFilename, file => file.document_id)
  const standard = createPushBatchItem({ document_id: 'standard', document_no: 'Z806C080A001.gen' })
  assert.equal(standard.segmentName, '806C')
  assert.equal(batchItemError(standard), '')
  const manual = { ...createPushBatchItem({ document_id: 'manual', document_no: 'custom.gen' }), material: 'A0', thickness: 8 }
  assert.equal(batchItemError(manual), '请填写分段名')
  manual.segmentName = '806C'
  assert.equal(batchItemError(manual), '')
  manual.segmentName = '806\nC'
  assert.match(batchItemError(manual), /控制字符/)
  assert.match(view, /title: "分段名"/)
  assert.match(view, /v-else v-model:value.trim="item.segmentName"/)
})

test('execute API sends the manual segment without changing the file reference', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), body: options.body })
    return { ok: true, json: async () => ({ success: true, data: { status: 'sent' } }) }
  }
  await executeCuttingDispatch('task/1', '806C')
  assert.deepEqual(calls, [{ url: '/api/v1/cutting/dispatches/task%2F1/execute', body: JSON.stringify({ segment_name: '806C' }) }])
})

test('history retries require a segment for nonstandard files and cancellation never executes', async () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const source = view.slice(view.indexOf('async function executeSaved('), view.indexOf('function formatTime('))
  const calls = [], retryDispatch = ref(null), retrySegment = ref('')
  const operatingId = ref('')
  let accepted = true
  const executeSaved = scriptFunction('confirm', 'retryDispatch', 'retrySegment', 'operatingId', 'machineName', 'parseCuttingInstructionFilename', 'executeCuttingDispatch', 'showNotice', 'loadHistory', 'refreshVisibleFileStatuses', `${source}\nreturn executeSaved`)(async () => accepted, retryDispatch, retrySegment, operatingId, String, parseCuttingInstructionFilename, async (...args) => { calls.push(args); return { remote_task_id: 'remote' } }, () => {}, async () => {}, async () => {})
  const manual = { dispatch_id: 'old-manual', document_no: 'custom.gen', machine_ip: '10.1.1.2' }
  await executeSaved(manual)
  assert.equal(calls.length, 0)
  assert.deepEqual(retryDispatch.value, manual)
  await executeSaved(manual, '806C')
  assert.deepEqual(calls, [['old-manual', '806C']])
  assert.equal(retryDispatch.value, null)
  accepted = false
  await executeSaved(manual, '806C')
  assert.equal(calls.length, 1)
  accepted = true
  await executeSaved({ ...manual, dispatch_id: 'uploaded', remote_task_id: 'remote' })
  assert.deepEqual(calls[1], ['uploaded', undefined])
  assert.doesNotMatch(view, /window\.(?:confirm|prompt)\(/)
})
