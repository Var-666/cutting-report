import assert from 'node:assert/strict'
import { afterEach, test } from 'node:test'
import { readFileSync } from 'node:fs'
import {
  listCuttingCatalogs,
  listCuttingFiles,
  listCuttingProjects,
} from '../src/api/reports.js'

const originalFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = originalFetch })

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
  assert.match(calls[0], /limit=100/)
  assert.match(calls[1], /project_id=project%2Fid/)
  assert.match(calls[2], /catalog_id=catalog%2Fid/)
  assert.match(calls[2], /keyword=PK-301/)
  assert.match(calls[2], /page=2/)
  assert.match(calls[2], /page_size=20/)
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
  assert.match(view, /const selectedProjectId = ref\(''\)/)
  assert.match(view, /<select id="project-selector"[\s\S]*v-for="project in projectOptions"/)
  assert.match(view, /选择支持的项目/)
  assert.doesNotMatch(view, /project-keyword|输入项目名称或船号/)
  assert.match(view, /文件名<\/th><th>中间产品<\/th><th>文件格式<\/th><th>版本<\/th><th>操作人<\/th><th>操作人工号<\/th><th>操作时间/)
  assert.match(view, /fileExtension\(row\)/)
  assert.doesNotMatch(view, /ConditionBuilder|SortBuilder|searchCuttingFiles|组合条件查询/)
  assert.match(node, /<CatalogTreeNode/)
  assert.match(node, /node\.children/)
})

test('project-shaped catalog root is hidden to avoid showing the project twice', () => {
  const view = readFileSync(new URL('../src/views/CuttingDispatchView.vue', import.meta.url), 'utf8')
  assert.match(view, /roots\.length === 1/)
  assert.match(view, /project\.project_name/)
  assert.match(view, /roots = roots\[0\]\.children/)
})
