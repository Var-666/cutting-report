import { renderComponent } from './vue-component-helpers.mjs'
import assert from 'node:assert/strict'
import { afterEach, test, vi } from 'vitest'
import { computed, ref } from 'vue'
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
import * as dispatch from '../src/api/dispatch.ts'
import { useCuttingDispatchBatch } from '../src/composables/useCuttingDispatchBatch'
import { useCuttingDispatchHistory } from '../src/composables/useCuttingDispatchHistory'
import { useCuttingFileBrowser } from '../src/composables/useCuttingFileBrowser'
import { parseCuttingInstructionFilename } from '../src/utils/cuttingInstructionName.ts'
import { cuttingFileKey as fileKey, matchesDispatchParameters, batchItemError, hasNameMismatch } from '../src/utils/cuttingDispatchBatch.ts'

const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  reportCache.invalidate('cutting:')
})

const flushDispatch = () => new Promise(resolve => setImmediate(resolve))

function historyPagination() {
  const inputs = { pageActive: ref(true), operatingId: ref(''), pushBatch: { saving: false } }
  const state = useCuttingDispatchHistory({ pageActive: inputs.pageActive, isBusy: () => Boolean(inputs.operatingId.value || inputs.pushBatch.saving) })
  return { ...inputs, ...state, historyPageCount: computed(() => Math.max(1, Math.ceil(state.historyTotal.value / 10))) }
}

test('dispatch history renders file references, machine names, status and safe fallback values', async () => {
  const render = histories => renderComponent('components/dispatch/DispatchHistoryTable', {
    histories, historyTotal: histories.length, historyPage: 1, historyPageSize: 10, loading: false,
    projectOptions: [{ project_id: 'P1', ship_no: 'SHIP-FALLBACK' }], machineName: () => '测试机床',
  })
  const item = { dispatch_id: 'task-1', project_id: 'P1', document_no: 'Z806C080A001.gen',
    quantity: 2, machine_ip: '192.0.2.1', status: 'failed', status_label: '派发失败',
    stage_label: '上传文件', last_error: '<script>unsafe</script>', created_at: '2026-10-28T08:00:00' }
  const html = await render([item])
  for (const value of ['SHIP-FALLBACK', '806C', item.document_no, '测试机床', '派发失败', 'task-1', '2026年10月28日']) assert.ok(html.includes(value), value)
  assert.ok(!html.includes('阶段'))
  assert.ok(!html.includes('<script>unsafe</script>'))
  assert.ok(html.includes('&lt;script&gt;unsafe&lt;/script&gt;'))
  assert.ok(!html.includes('取消指派'))
  const explicit = await render([{ ...item, ship_no: 'SHIP-EXPLICIT', segment_name: 'SEG-EXPLICIT', remote_task_id: 'remote-1', task_identifier: 'identifier-1' }])
  for (const value of ['SHIP-EXPLICIT', 'SEG-EXPLICIT', 'remote-1', 'identifier-1']) assert.ok(explicit.includes(value), value)
  assert.ok(html.includes('文件位置'))
  assert.ok(html.includes('未记录'))
  const location = '18600-2 / 船体 / 船体 / 410S / 板材 / Gen指令'
  const located = await render([{ ...item, file_location: location }])
  assert.ok(located.includes(location))
  assert.ok(located.includes(`title="${location}"`))
  const completed = await render([{...item,status:'sent',status_label:'已派发',processing_status:'completed',processing_finished_at:'2026-11-05T16:20:30+08:00',processing_finish_by:'20kW切割机(10.1.133.197)'}])
  assert.ok(completed.includes('已派发') && completed.includes('已完成'))
  assert.ok(completed.includes('加工完成时间：') && completed.includes('20kW切割机'))
  assert.ok((await render([])).includes('暂无数据'))
})

test('dispatch history pages through all records in ten-row pages independently of file selection', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    const query = new URL(String(url), 'http://localhost').searchParams
    const page = Number(query.get('page'))
    calls.push({ page, size: Number(query.get('page_size')) })
    const items = Array.from({ length: page === 3 ? 3 : 10 }, (_, index) => ({ dispatch_id: `${page}-${index}` }))
    return Response.json({ success: true, data: { items, total: 23, page } })
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
    return Response.json({ success: true, data: { items: page === 1 ? [{ dispatch_id: 'remaining' }] : [], total: 1, page } })
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
  resolveResponse(Response.json({ success: true, data: { items: [{ dispatch_id: 'late' }], total: 1, page: 1 } }))
  await pending
  assert.equal(history.histories.value.length, 0)
  assert.equal(history.historyError.value, '')
  assert.equal(history.historyLoading.value, false)
})

test('directory browser requests projects, one project tree, then one catalog page', async () => {
  const calls = []
  globalThis.fetch = async (url) => {
    calls.push(String(url))
    return Response.json({ success: true, data: { items: [], tree: [], total: 0 } })
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
    return Response.json({ success: true, data: { items: [], tree: [] } })
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
    return Response.json({ success: true, data: {} })
  }

  await getCuttingStatus('push')

  assert.equal(calls.length, 1)
  assert.match(calls[0], /^\/api\/v1\/cutting\/status\?scope=push$/)
})

test('an explicit readiness retry bypasses a cached unconfigured status', async () => {
  let requests = 0
  globalThis.fetch = async () => Response.json({ success: true, data: { can_push: ++requests > 1 } })
  assert.equal((await getCuttingStatus('push')).can_push, false)
  assert.equal((await getCuttingStatus('push')).can_push, false)
  assert.equal((await getCuttingStatus('push', { force: true })).can_push, true)
  assert.equal(requests, 2)
})

test('file status refresh preserves selections and ignores stale responses after a folder switch', async () => {
  const original = selectableFile('original')
  const next = selectableFile('new-folder')
  const pending = []
  const browser = fileSelection([original], { listFiles: (query, options) => query.catalogId === 'C2'
    ? Promise.resolve({ items: [next], total: 1, page: 1 })
    : new Promise(resolve => pending.push({ query, options, resolve })) })
  browser.appliedFileKeyword.value = '307C'
  browser.page.value = 3
  browser.setFileSelected(original, true)
  const first = browser.refreshVisibleFileStatuses()
  const status = { status: 'sent', status_label: '已派发', stage_label: '推送完成' }
  assert.deepEqual(pending[0].query, { projectId: 'P1', catalogId: 'C1', keyword: '307C', page: 3, pageSize: 10 })
  assert.deepEqual(pending[0].options, { force: true })
  pending[0].resolve({ items: [{ ...original, dispatch_status: status }] })
  await first
  assert.deepEqual(browser.rows.value[0].dispatch_status, status)
  assert.equal(browser.selectedFiles.value.has(fileKey(original)), true)
  const stale = browser.refreshVisibleFileStatuses()
  await browser.selectCatalog({ catalog_id: 'C2', title: '新目录' })
  pending[1].resolve({ items: [{ ...next, dispatch_status: status }] })
  await stale
  assert.deepEqual(browser.rows.value, [next])
})

test('dispatch mutations invalidate cached file status even when execution fails', async () => {
  let reads = 0
  globalThis.fetch = async (url, options) => {
    if (options.method === 'POST') {
      if (String(url).endsWith('/execute')) throw new Error('推送失败')
      return Response.json({ success: true, data: {} })
    }
    reads += 1
    return Response.json({ success: true, data: { items: [], total: reads } })
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
  const notices = []
  const inputs = { pageActive: ref(true), systemStatus: ref({ can_push: true }), pushBatch: { saving: false } }
  if (listFiles) vi.spyOn(dispatch, 'listCuttingFiles').mockImplementation(listFiles)
  const state = useCuttingFileBrowser({ ...inputs, isDispatching: () => inputs.pushBatch.saving, initialProjectId: '', rememberSelectedProject() {}, notice: ref(''), showNotice: text => notices.push(text) })
  state.rows.value = files
  state.total.value = total
  state.selectedProject.value = { project_id: 'P1' }
  state.selectedCatalog.value = { catalog_id: 'C1' }
  return { ...inputs, ...state, fileKey, notices }
}

const selectableFile = (id, documentNo = 'S806C080A001.gen') => ({ document_id: String(id), version_id: 'v1', normalized_path: `files/${id}.gen`, document_no: documentNo, path_available: true })
const checkboxEvent = checked => checked

test('directory file results fetch ten rows per page with the applied filter', async () => {
  const files = Array.from({ length: 23 }, (_, index) => selectableFile(index))
  const calls = []
  const selection = fileSelection([], { listFiles: async ({ page, pageSize, keyword }) => {
    calls.push([page, pageSize, keyword])
    return { items: files.slice((page - 1) * pageSize, page * pageSize), total: files.length }
  } })
  selection.fileKeyword.value = '装配'
  await selection.applyFileSearch()
  assert.equal(selection.rows.value.length, 10)
  await selection.changePage(2)
  assert.equal(selection.rows.value[0].document_id, '10')
  await selection.changePage(3)
  assert.equal(selection.rows.value.length, 3)
  assert.deepEqual(calls, [[1, 10, '装配'], [2, 10, '装配'], [3, 10, '装配']])
})

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
  assert.equal(selection.allCatalogFilesSelected.value, false)
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
  const batch = batchWorkflow()
  const createPushBatchItem = file => {
    batch.closePushBatch()
    batch.inputs.selectedFiles.value = new Map([[file.document_id, file]])
    batch.openPushBatch()
    return batch.pushBatch.items[0]
  }
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

test('non-standard names require manual confirmation and keep their independent dispatch reference', async () => {
  const batch = batchWorkflow()
  batch.closePushBatch()
  batch.inputs.selectedFiles.value = new Map([['manual', selectableFile('manual', 'custom.gen')]])
  batch.openPushBatch()
  Object.assign(batch.pushBatch.items[0], { segmentName: '806C', material: 'A0', thickness: 8 })
  await batch.submitPushBatch()
  assert.equal(batch.calls.create.length, 0)
  assert.match(batch.pushBatch.error, /人工核对/)
  batch.pushBatch.nonStandardConfirmed = true
  await batch.submitPushBatch()
  assert.deepEqual(batch.calls.create[0], { machine_ip: '10.147.1.40', items: [{
    document_id: 'manual', version_id: 'v1', file_path: 'files/manual.gen',
    material: 'A0', thickness: 8, quantity: 1, name_check_confirmed: true,
  }] })
})

test('changing standard-name parameters requires a fresh override confirmation before sending', async () => {
  const batch = batchWorkflow()
  const item = batch.pushBatch.items[0]
  item.thickness = 9
  await batch.submitPushBatch()
  assert.equal(batch.calls.create.length, 0)
  assert.match(batch.pushBatch.error, /参数与标准文件名不一致/)
  batch.pushBatch.overrideConfirmed = true
  batch.invalidateBatchConfirmation(item)
  assert.equal(batch.pushBatch.overrideConfirmed, false)
  batch.pushBatch.overrideConfirmed = true
  await batch.submitPushBatch()
  assert.equal(batch.calls.create[0].items[0].name_check_confirmed, true)
  assert.equal(batch.calls.create[0].items[0].thickness, 9)
  assert.equal(batch.calls.create[0].items[1].name_check_confirmed, false)
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
    return Response.json({ success: true, data: { ...current, can_execute: false, stage: 'upload_unknown' } })
  }
  const status = await getCuttingDispatch('task/1')
  assert.equal(status.can_execute, false)
  assert.deepEqual(calls, ['/api/v1/cutting/dispatches/task%2F1'])

})

test('dispatch segments are recognized or required before a nonstandard file can be pushed', () => {
  const batch = batchWorkflow()
  const createPushBatchItem = file => {
    batch.closePushBatch()
    batch.inputs.selectedFiles.value = new Map([[file.document_id, file]])
    batch.openPushBatch()
    return batch.pushBatch.items[0]
  }
  const standard = createPushBatchItem({ document_id: 'standard', document_no: 'Z806C080A001.gen' })
  assert.equal(standard.segmentName, '806C')
  assert.equal(batchItemError(standard), '')
  const manual = { ...createPushBatchItem({ document_id: 'manual', document_no: 'custom.gen' }), material: 'A0', thickness: 8 }
  assert.equal(batchItemError(manual), '请填写分段名')
  manual.segmentName = '806C'
  assert.equal(batchItemError(manual), '')
  manual.segmentName = '806\nC'
  assert.match(batchItemError(manual), /控制字符/)
})

test('execute API sends the manual segment without changing the file reference', async () => {
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url: String(url), body: options.body })
    return Response.json({ success: true, data: { status: 'sent' } })
  }
  await executeCuttingDispatch('task/1', '806C')
  assert.deepEqual(calls, [{ url: '/api/v1/cutting/dispatches/task%2F1/execute', body: JSON.stringify({ segment_name: '806C' }) }])
})

function batchWorkflow(api = {}) {
  const calls = { create: [], execute: [], refresh: 0 }
  const inputs = {
    selectedFiles: ref(new Map([['one', selectableFile('one')], ['two', selectableFile('two')]])),
    dispatchForm: { machineIp: '10.147.1.40' }, systemStatus: ref({ can_push: true }), operatingId: ref(''),
    loadHistory: async () => { calls.refresh++ }, refreshVisibleFileStatuses: async () => { calls.refresh++ },
    loadFiles: async () => {}, showNotice() {},
  }
  inputs.clearSelectedFiles = () => { inputs.selectedFiles.value = new Map() }
  const requests = {
    createCuttingDispatchBatch: async payload => {
      calls.create.push(payload)
      return { items: payload.items.map((item, index) => ({ ...item, status: 'created', dispatch: { dispatch_id: `d${index + 1}` } })) }
    },
    executeCuttingDispatch: async id => { calls.execute.push(id); return {} },
    getCuttingDispatch: async () => { throw new Error('状态不可用') },
    ...api,
  }
  for (const [name, implementation] of Object.entries(requests)) vi.spyOn(dispatch, name).mockImplementation(implementation)
  const state = useCuttingDispatchBatch(inputs)
  state.openPushBatch()
  return { ...state, inputs, calls }
}

test('batch composable preserves sequential execution, blocks concurrent submit and refreshes both result consumers', async () => {
  const pending = []
  const batch = batchWorkflow({ executeCuttingDispatch: id => new Promise(resolve => pending.push({ id, resolve })) })
  const run = batch.submitPushBatch()
  await flushDispatch()
  assert.equal(batch.pushBatch.saving, true)
  assert.deepEqual(pending.map(item => item.id), ['d1'])
  await batch.submitPushBatch()
  assert.equal(batch.calls.create.length, 1)
  assert.deepEqual(batch.calls.create[0].items.map(item => item.document_id), ['one', 'two'])
  pending[0].resolve({})
  await flushDispatch()
  assert.deepEqual(pending.map(item => item.id), ['d1', 'd2'])
  pending[1].resolve({})
  await run
  assert.equal(batch.pushBatch.saving, false)
  assert.equal(batch.pushBatch.resultsReady, true)
  assert.equal(batch.pushBatch.completed, 2)
  assert.equal(batch.inputs.selectedFiles.value.size, 0)
  assert.equal(batch.calls.refresh, 2)
  assert.deepEqual(batch.pushBatch.items.map(item => item.status), ['sent', 'sent'])
})

test('batch composable validates before creating tasks and preserves an uncertain execution for explicit status checking', async () => {
  const batch = batchWorkflow({ executeCuttingDispatch: async () => { throw new Error('连接中断') } })
  batch.pushBatch.items[0].segmentName = ''
  await batch.submitPushBatch()
  assert.equal(batch.calls.create.length, 0)
  assert.match(batch.pushBatch.error, /参数不完整/)
  batch.pushBatch.items[0].segmentName = '806C'
  await batch.submitPushBatch()
  assert.equal(batch.calls.create.length, 1)
  assert.equal(batch.pushBatch.saving, false)
  assert.equal(batch.inputs.operatingId.value, '')
  assert.equal(batch.pushBatch.resultsReady, true)
  assert.ok(batch.pushBatch.items.every(item => item.status === 'failed' && !item.retryable && /不要重复上传/.test(item.error)))
})
