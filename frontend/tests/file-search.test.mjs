import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import {
  listCuttingCatalogs,
  listCuttingFiles,
  listCuttingProjects,
} from '../src/api/reports.js'
import { reportCache } from '../src/api/reportCache.js'
import { parseCuttingInstructionFilename } from '../src/utils/cuttingInstructionName.js'

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

test('file download page uses the recursive directory tree without condition builders', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const node = readFileSync(new URL('../src/components/CatalogTreeNode.vue', import.meta.url), 'utf8')

  assert.match(view, /<CatalogTreeNode/)
  assert.match(view, /listCuttingProjects/)
  assert.match(view, /listCuttingCatalogs/)
  assert.match(view, /listCuttingFiles/)
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
  assert.match(view, /文件名<\/th><th>中间产品<\/th><th>文件格式<\/th><th>版本<\/th><th>操作人<\/th><th>操作人工号<\/th><th>操作时间/)
  assert.match(view, /fileExtension\(row\)/)
  assert.doesNotMatch(view, /ConditionBuilder|SortBuilder|searchCuttingFiles|组合条件查询/)
  assert.match(node, /<CatalogTreeNode/)
  assert.match(node, /node\.children/)
})

test('selected download project survives refresh and stays synchronized', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  const downloads = readFileSync(new URL('../src/components/CuttingDownloadsPanel.vue', import.meta.url), 'utf8')

  assert.match(view, /useRoute, useRouter/)
  assert.match(view, /route\.query\.project_id/)
  assert.match(view, /router\.replace\(\{ query \}\)/)
  assert.match(view, /if \(projectToOpen && sequence === querySequence\) await selectProject\(projectToOpen\)/)
  assert.match(view, /:project-id="selectedProjectId"/)
  assert.match(view, /@project-change="selectProjectById"/)
  assert.match(downloads, /defineProps\(\{ projectId:/)
  assert.match(downloads, /watch\(\(\) => props\.projectId/)
  assert.match(downloads, /emit\('project-change', selectedProjectId\.value\)/)
})

test('project-shaped catalog root is hidden to avoid showing the project twice', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /roots\.length === 1/)
  assert.match(view, /project\.project_name/)
  assert.match(view, /roots = roots\[0\]\.children/)
})

test('file download table supports multi-select and batch save', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /const selectedDownloadFiles = ref\(new Map\(\)\)/)
  assert.match(view, /function toggleVisibleDownloads/)
  assert.match(view, /async function downloadSelectedFiles/)
  assert.match(view, /aria-label="全选当前页可保存文件"/)
  assert.match(view, /:aria-label="`选择文件 \$\{row\.document_no\}`"/)
  assert.match(view, /批量保存到系统/)
  assert.match(view, /for \(const \[index, file\] of files\.entries\(\)\)/)
  assert.match(view, /setDownloadSelected\(file, false\)/)
  assert.match(view, /colspan="10"/)
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

test('file push applies parsed standard-name values without changing non-standard files', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')

  assert.match(view, /parseCuttingInstructionFilename\(activeFile\.value\?\.filename\)/)
  assert.match(view, /dispatchForm\.material = parsed\.material/)
  assert.match(view, /dispatchForm\.thickness = parsed\.thickness/)
  assert.match(view, /if \(!parsed\) return/)
  assert.match(view, /已按标准名称填充/)
})
