<script setup>
import { computed, onMounted, ref } from 'vue'
import CatalogTreeNode from './CatalogTreeNode.vue'
import { listCuttingCatalogs, listCuttingProjects, searchCuttingDownloads } from '../api/reports'

const defaultProjectKeyword = '君正25900-9SS'
const items = ref([])
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const directoryLoading = ref(false)
const error = ref('')
const selectedProject = ref(null)
const catalogTree = ref([])
const selectedCatalog = ref(null)
const expandedCatalogIds = ref(new Set())
const fileKeyword = ref('')
const appliedFileKeyword = ref('')
const pageSize = 20
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
let querySequence = 0

function bytes(value) {
  if (value == null) return '--'
  if (value < 1024) return `${value} B`
  return value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`
}

function catalogPath(node) {
  const names = [node.title]
  const byId = new Map()
  const visit = (nodes) => nodes.forEach((item) => {
    byId.set(String(item.catalog_id), item)
    visit(item.children || [])
  })
  visit(catalogTree.value)
  let parent = byId.get(String(node.parent_id))
  const seen = new Set([String(node.catalog_id)])
  while (parent && !seen.has(String(parent.catalog_id))) {
    names.unshift(parent.title)
    seen.add(String(parent.catalog_id))
    parent = byId.get(String(parent.parent_id))
  }
  return names.join(' / ')
}

function expandCatalog(node) {
  const next = new Set(expandedCatalogIds.value)
  const key = String(node.catalog_id)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedCatalogIds.value = next
}

async function refresh(nextPage = page.value) {
  if (!selectedProject.value) {
    items.value = []
    total.value = 0
    page.value = 1
    return
  }
  const keyword = fileKeyword.value.trim()
  const criteria = {
    logic: 'and',
    rules: [
      { field: 'project_id', operator: 'eq', value: String(selectedProject.value.project_id) },
      ...(selectedCatalog.value
        ? [{ field: 'catalog_id', operator: 'eq', value: String(selectedCatalog.value.catalog_id) }]
        : []),
      ...(keyword ? [{ field: 'filename', operator: 'contains', value: keyword }] : []),
    ],
  }
  const sequence = ++querySequence
  loading.value = true
  error.value = ''
  try {
    const data = await searchCuttingDownloads(criteria, { page: nextPage, pageSize })
    if (sequence !== querySequence) return
    items.value = data.items || []
    total.value = data.total || 0
    page.value = nextPage
    appliedFileKeyword.value = keyword
  } catch (reason) {
    if (sequence === querySequence) error.value = reason.message
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

async function selectCatalog(node) {
  selectedCatalog.value = node
  if (node.children?.length && !expandedCatalogIds.value.has(String(node.catalog_id))) expandCatalog(node)
  fileKeyword.value = ''
  appliedFileKeyword.value = ''
  await refresh(1)
}

async function selectAllFiles() {
  selectedCatalog.value = null
  fileKeyword.value = ''
  appliedFileKeyword.value = ''
  await refresh(1)
}

async function loadDirectory() {
  directoryLoading.value = true
  error.value = ''
  try {
    const projectData = await listCuttingProjects(defaultProjectKeyword)
    const projects = projectData.items || []
    selectedProject.value = projects.find((item) =>
      String(item.project_name || '').trim() === defaultProjectKeyword
      || String(item.ship_no || '').trim() === defaultProjectKeyword
    ) || projects[0] || null
    if (!selectedProject.value) {
      catalogTree.value = []
      return
    }
    const catalogData = await listCuttingCatalogs(selectedProject.value.project_id)
    let roots = catalogData.tree || []
    if (roots.length === 1 && roots[0].children?.length
        && String(roots[0].title || '').trim() === String(selectedProject.value.project_name || '').trim()) {
      roots = roots[0].children
    }
    catalogTree.value = roots
    expandedCatalogIds.value = new Set(roots.map((item) => String(item.catalog_id)))
    await refresh(1)
  } catch (reason) {
    error.value = reason.message
  } finally {
    directoryLoading.value = false
  }
}

defineExpose({ refresh: () => refresh(1) })
onMounted(loadDirectory)
</script>

<template>
  <section class="panel download-panel" aria-label="系统已保存文件目录检索">
    <div class="panel-title">
      <div><h2>系统已保存文件</h2></div>
      <p>{{ selectedProject ? `共 ${total} 条 · 当前第 ${page} 页` : '正在读取项目' }}</p>
    </div>
    <div class="saved-file-workspace">
      <aside class="saved-directory-panel">
        <div class="saved-directory-title">
          <strong>{{ selectedProject?.project_name || defaultProjectKeyword }}</strong>
          <small>{{ selectedProject?.ship_no || '目录检索' }}</small>
        </div>
        <div class="saved-directory-scroll" :aria-busy="directoryLoading">
          <div v-if="directoryLoading" class="saved-directory-message">正在读取目录…</div>
          <template v-else>
            <button type="button" class="saved-all-files" :class="{ selected: !selectedCatalog }" @click="selectAllFiles">
              <span class="saved-all-files-icon" aria-hidden="true"></span>
              <strong>全部已保存文件</strong>
              <small>{{ total }} 条</small>
            </button>
            <ul v-if="catalogTree.length" class="saved-directory-tree" role="tree" aria-label="系统已保存文件目录">
              <CatalogTreeNode
                v-for="node in catalogTree"
                :key="node.catalog_id"
                :node="node"
                :selected-id="String(selectedCatalog?.catalog_id || '')"
                :expanded-ids="expandedCatalogIds"
                @select="selectCatalog"
                @toggle="expandCatalog"
              />
            </ul>
            <div v-else class="saved-directory-message">没有找到项目目录</div>
          </template>
        </div>
      </aside>

      <div class="saved-file-results">
        <div class="selected-directory">
          <span>当前位置</span>
          <strong>{{ selectedCatalog ? `${selectedProject.project_name} / ${catalogPath(selectedCatalog)}` : `${selectedProject?.project_name || defaultProjectKeyword} / 全部已保存文件` }}</strong>
        </div>
        <form class="saved-file-search" @submit.prevent="refresh(1)">
          <label for="saved-file-keyword">{{ selectedCatalog ? '当前目录内查找文件' : '在已保存文件中查找' }}</label>
          <div><input id="saved-file-keyword" v-model.trim="fileKeyword" :disabled="!selectedProject" placeholder="输入系统文件名" /><button type="submit" :disabled="!selectedProject || loading">{{ loading ? '查询中…' : '查询' }}</button></div>
        </form>
        <p v-if="error" class="download-error" role="alert">{{ error }}</p>
        <div class="download-table-wrap" :aria-busy="loading">
          <div v-if="loading" class="saved-file-loading">正在加载第 {{ page }} 页…</div>
          <table v-content-table class="download-table">
            <thead><tr><th>系统文件名</th><th>版本 / 来源</th><th>大小</th><th>保存时间 / 操作人</th><th>状态</th></tr></thead>
            <tbody>
              <tr v-for="item in items" :key="item.download_id">
                <td class="download-name"><strong :title="item.filename">{{ item.filename }}</strong><small :title="item.relative_path">{{ item.relative_path }}</small><small v-if="item.error" class="download-row-error">{{ item.error }}</small></td>
                <td>{{ item.version }}<small>{{ item.is_simulation ? '模拟文件 · 不用于生产' : item.source_mode === 'http' ? 'FO 文件服务' : '本地目录' }}</small></td>
                <td>{{ bytes(item.size) }}</td>
                <td>{{ item.created_at.replace('T', ' ').slice(0, 19) }}<small>{{ item.created_by_name }}</small></td>
                <td><span class="download-badge" :class="{ failed: item.status === 'failed' || (item.status === 'downloaded' && !item.available), success: item.available }">{{ item.status_label }}</span></td>
              </tr>
              <tr v-if="!loading && !items.length"><td colspan="5" class="download-empty">{{ appliedFileKeyword ? '没有匹配的系统文件' : selectedCatalog ? '当前目录还没有保存文件' : '当前项目还没有保存文件' }}</td></tr>
            </tbody>
          </table>
        </div>
        <div class="download-pagination"><span>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</span><button :disabled="page <= 1 || loading" @click="refresh(page - 1)">上一页</button><button :disabled="page >= pages || loading" @click="refresh(page + 1)">下一页</button></div>
        <p class="download-hint">这些文件保存在系统服务器中，可在“文件推送”页面选择并推送到魔盒。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.download-panel { margin-top:22px; min-width:0; }
.saved-file-workspace { display:grid; grid-template-columns:minmax(260px,.42fr) minmax(0,1.58fr); min-height:430px; }
.saved-directory-panel { min-width:0; border-right:1px solid #dfe8ed; background:#fbfcfd; }
.saved-directory-title { display:flex; flex-direction:column; gap:3px; min-height:61px; padding:14px 16px; border-bottom:1px solid #e3eaee; background:#f1f8fa; }
.saved-directory-title strong { overflow:hidden; color:#273f50; text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.saved-directory-title small { color:#7b8d98; font-size:9px; }
.saved-directory-scroll { min-height:368px; max-height:610px; overflow:auto; padding:10px 9px 15px; }
.saved-directory-tree { margin:0; padding:0; list-style:none; }
.saved-all-files { width:100%; min-height:42px; display:grid; grid-template-columns:20px minmax(0,1fr) auto; align-items:center; gap:8px; margin-bottom:4px; padding:7px 10px; color:#344f60; border:0; border-left:3px solid transparent; background:transparent; text-align:left; }
.saved-all-files:hover,.saved-all-files.selected { color:#0b6384; border-left-color:#16a2b8; background:#e5f5f8; }
.saved-all-files strong { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
.saved-all-files small { color:#81929c; font-size:9px; }
.saved-all-files-icon { width:17px; height:13px; position:relative; border:1px solid #6db4ca; background:#eef9fc; }
.saved-all-files-icon::before { content:''; position:absolute; left:1px; top:-5px; width:8px; height:4px; border:1px solid #6db4ca; border-bottom:0; background:#eef9fc; }
.saved-directory-message { padding:28px 12px; color:var(--muted); text-align:center; font-size:11px; }
.saved-file-results { min-width:0; display:flex; flex-direction:column; }
.selected-directory { display:flex; align-items:center; gap:10px; min-height:61px; padding:12px 18px; border-bottom:1px solid #e4eaee; background:#f1f8fa; }
.selected-directory span { flex:0 0 auto; color:#718895; font-size:9px; font-weight:700; letter-spacing:1px; }
.selected-directory strong { min-width:0; overflow:hidden; color:#155d7b; text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
.saved-file-search { display:flex; align-items:end; justify-content:space-between; gap:18px; padding:15px 18px; border-bottom:1px solid #e3eaee; background:#f8fafb; }
.saved-file-search label { flex:0 0 auto; color:#506775; font-size:10px; font-weight:700; }
.saved-file-search > div { display:flex; width:min(430px,100%); gap:7px; }
.saved-file-search input { width:100%; min-width:0; }
.saved-file-search button { min-width:58px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; }
button { min-height:34px; padding:7px 12px; border:1px solid #c3d3dc; background:white; color:var(--blue); cursor:pointer; font-size:11px; }
button:disabled { opacity:.55; cursor:not-allowed; }
.download-hint,.download-error { margin:14px 22px; padding:11px 13px; font-size:12px; line-height:1.65; background:#eef7fa; color:#26596d; overflow-wrap:anywhere; }
.download-error { background:#fff0ee; color:#913a31; }
.download-table-wrap { position:relative; flex:1 1 auto; min-height:160px; overflow-x:auto; }
.saved-file-loading { position:absolute; z-index:2; inset:0; display:flex; align-items:flex-start; justify-content:center; padding-top:48px; background:rgba(247,251,253,.8); color:var(--blue); font-size:12px; }
.download-table { width:100%; border-collapse:collapse; font-size:11px; }
.download-table th { padding:12px 14px; text-align:left; color:#dfedf4; background:#0b4f76; white-space:nowrap; }
.download-table td { padding:13px 14px; border-bottom:1px solid #e4eaee; }
.download-table td small { display:block; margin-top:4px; color:var(--muted); font-size:10px; }
.download-name { max-width:340px; }
.download-name strong,.download-name small { display:block; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.download-table td small.download-row-error,.download-row-error { color:#963d30; }
.download-badge { padding:5px 8px; border-radius:20px; color:#8b5d12; background:#fff3d8; white-space:nowrap; }
.download-badge.success { color:#267048; background:#e8f7ef; }
.download-badge.failed { color:#963d30; background:#fff0ee; }
.download-empty { text-align:center; padding:30px!important; color:var(--muted); }
.download-pagination { display:flex; justify-content:flex-end; gap:12px; align-items:center; padding:15px 22px 0; font-size:11px; color:var(--muted); }
@media(max-width:900px) {
  .saved-file-workspace { grid-template-columns:1fr; }
  .saved-directory-panel { border-right:0; border-bottom:1px solid #dfe8ed; }
  .saved-directory-scroll { min-height:220px; max-height:360px; }
}
</style>
