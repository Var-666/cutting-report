import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import {
  getCuttingStatus,
  getCuttingDispatch,
  listCuttingCatalogs,
  listCuttingFiles,
  listCuttingProjects,
} from '../src/api/reports.js'
import { reportCache } from '../src/api/reportCache.js'
import { parseCuttingInstructionFilename } from '../src/utils/cuttingInstructionName.js'
import { matchesDispatchParameters } from '../src/utils/cuttingDispatchBatch.js'

const originalFetch = globalThis.fetch
afterEach(() => {
  globalThis.fetch = originalFetch
  reportCache.invalidate('cutting:')
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

test('file dispatch page uses the recursive PDM directory tree without condition builders', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const node = readFileSync(new URL('../src/components/CatalogTreeNode.vue', import.meta.url), 'utf8')

  assert.match(view, /<CatalogTreeNode/)
  assert.match(view, /listCuttingProjects/)
  assert.match(view, /listCuttingCatalogs/)
  assert.match(view, /listCuttingFiles/)
  assert.match(view, /getCuttingStatus\('push', \{ signal \}\)/)
  assert.doesNotMatch(view, /defineProps|isDownload/)
  assert.match(view, /项目目录/)
  assert.match(view, /当前位置/)
  assert.match(view, /const pageSize = 20/)
  assert.match(view, /const projectOptions = ref\(\[\]\)/)
  assert.match(view, /const projectKeyword = ref\(''\)/)
  assert.match(view, /const filteredProjectOptions = computed/)
  assert.match(view, /const selectedProjectId = ref\(initialProjectId\)/)
  assert.match(view, /id="project-keyword"[\s\S]*:disabled="projectLoading"[\s\S]*@input="applyProjectKeyword"/)
  assert.match(view, /<AppSelect id="project-selector"[\s\S]*v-for="project in filteredProjectOptions"/)
  assert.match(view, /支持项目/)
  assert.match(view, /项目名称或船号/)
  assert.doesNotMatch(view, /<option value="" disabled>\{\{ projectLoading/)
  assert.match(view, /\.project-search input,\.project-search \.app-select \{ height:36px;[^}]*font-size:12px;/)
  assert.match(view, /\.project-search \.app-select__trigger \{ padding:0 10px; \}/)
  assert.match(view, /文件名<\/th><th>命名检查<\/th><th>中间产品<\/th><th>文件格式<\/th><th>版本<\/th><th>操作人<\/th><th>操作人工号<\/th><th>操作时间<\/th><th class="file-download-cell">操作/)
  assert.doesNotMatch(view, /<th>切割状态<\/th>/)
  assert.match(view, /fileExtension\(row\)/)
  assert.doesNotMatch(view, /ConditionBuilder|SortBuilder|searchCuttingFiles|组合条件查询/)
  assert.match(node, /<CatalogTreeNode/)
  assert.match(node, /node\.children/)
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

test('PDM file table supports up to twenty selections and direct batch dispatch', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /const selectedFiles = ref\(new Map\(\)\)/)
  assert.match(view, /function toggleVisibleFiles/)
  assert.match(view, /next\.size >= 20/)
  assert.match(view, /aria-label="全选当前页可派发文件"/)
  assert.match(view, /:aria-label="`选择文件 \$\{row\.document_no\}`"/)
  assert.match(view, /设置参数并派发/)
  assert.match(view, /@click="openSinglePush\(row\)"/)
  assert.match(view, /colspan="10"/)
  assert.doesNotMatch(view, /downloadCuttingFile|保存到系统|listCuttingDownloads/)
  assert.match(view, /class="dispatch-table source-file-table"/)
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
  assert.match(view, /\.dispatch-workspace\.download-workspace \{ grid-template-columns:248px minmax\(0,1fr\)/)
  assert.match(view, /\.dispatch-confirm-panel \{[^}]*grid-column:1\/-1;[^}]*align-self:start;/)
  assert.match(view, /\.directory-panel \{[^}]*display:grid;[^}]*grid-template-rows:auto auto minmax\(0,1fr\);/)
  assert.match(view, /\.directory-panel \{[^}]*grid-template-columns:minmax\(0,1fr\);/)
  assert.match(view, /\.directory-panel \.project-select-row \.app-select \{[^}]*flex:1 1 0;/)
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
    material: 'A',
    sequence: '01',
  })
  assert.equal(parseCuttingInstructionFilename('s201c090a204.txt').material, 'AH32')
  assert.equal(parseCuttingInstructionFilename('W102C125A606.GEN').material, 'AH36')
  assert.equal(parseCuttingInstructionFilename('L301C080SS12.GEN').material, '双相不锈钢')
  assert.equal(parseCuttingInstructionFilename('L301C080SS12.GEN').thickness, 8)
})

test('non-standard cutting instruction names are ignored', () => {
  for (const filename of [
    'PK-301CPS1.GEN',
    'prefix-P101C100A001.GEN',
    'P101C100A000.GEN',
    'P101C100A701.GEN',
    'P101C100A001.PDF',
  ]) {
    assert.equal(parseCuttingInstructionFilename(filename), null)
  }
})

test('direct dispatch builds independent PDM references and executes tasks sequentially', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /next\.size >= 20/)
  assert.match(view, /function createPushBatchItem\(file\)/)
  assert.match(view, /const parsed = parseCuttingInstructionFilename\(filename\)/)
  assert.match(view, /material: parsed\?\.material \|\| ''/)
  assert.match(view, /thickness: parsed\?\.thickness \?\? ''/)
  assert.match(view, /quantity: 1/)
  assert.match(view, /createCuttingDispatchBatch/)
  assert.match(view, /document_id: item\.documentId/)
  assert.match(view, /version_id: item\.versionId/)
  assert.match(view, /file_path: item\.filePath/)
  assert.match(view, /for \(const item of createdItems\)/)
  assert.match(view, /await executeCuttingDispatch\(item\.dispatchId\)/)
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
  assert.match(view, /@input="invalidateBatchConfirmation\(item\)"/)
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
