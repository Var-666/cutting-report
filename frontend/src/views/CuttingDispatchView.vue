<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import CuttingDownloadsPanel from '../components/CuttingDownloadsPanel.vue'
import ConditionBuilder from '../components/ConditionBuilder.vue'
import {
  createCuttingDispatch,
  getCuttingStatus,
  listCuttingDispatches,
  getConditionSchema,
  searchCuttingFiles,
} from '../api/reports'

const props = defineProps({ mode: { type: String, required: true, validator: (value) => ['download', 'push'].includes(value) } })
const isDownload = computed(() => props.mode === 'download')
const filters = ref({ logic: 'and', rules: [] })
const conditionSchema = ref(null)
const appliedFilters = ref(null)
let querySequence = 0
const dispatchForm = reactive({ machineIp: '', material: '', thickness: '', quantity: 1 })
const systemStatus = ref(null)
const rows = ref([])
const histories = ref([])
const selectedId = ref('')
const loading = ref(false)
const saving = ref(false)
const notice = ref('')
const noticeType = ref('info')
const page = ref(1)
const pageSize = 10
const total = ref(0)

const filtersDirty = computed(() => !appliedFilters.value || JSON.stringify(filters.value) !== JSON.stringify(appliedFilters.value))
const activeFile = computed(() => !filtersDirty.value && !loading.value ? rows.value.find((row) => row.document_id === selectedId.value) || null : null)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const physicalPath = computed(() => {
  if (systemStatus.value?.file_source?.mode === 'http') return ''
  const root = systemStatus.value?.file_server?.physical_root
  const relative = activeFile.value?.normalized_path
  if (!root || !relative) return ''
  return `${root.replace(/[\\/]+$/, '')}\\${relative.replaceAll('/', '\\')}`
})

function showNotice(message, type = 'info') {
  notice.value = message
  noticeType.value = type
}

function selectFile(documentId) {
  if (filtersDirty.value || loading.value) return
  selectedId.value = documentId
}

async function loadHistory() {
  if (isDownload.value) return
  const data = await listCuttingDispatches({ page: 1, pageSize: 10 })
  histories.value = data.items
}

async function searchFiles(resetPage = true, requestedPage = page.value) {
  if (!filters.value.rules.length) {
    showNotice('请添加至少一条检索条件后查询，不会加载全部文件。', 'error')
    return
  }
  const nextPage = resetPage || filtersDirty.value ? 1 : requestedPage
  const criteria = JSON.parse(JSON.stringify(filters.value))
  const sequence = ++querySequence
  loading.value = true
  try {
    const data = await searchCuttingFiles(criteria, { page: nextPage, pageSize })
    if (sequence !== querySequence || JSON.stringify(criteria) !== JSON.stringify(filters.value)) return
    rows.value = data.items
    total.value = data.total
    page.value = nextPage
    appliedFilters.value = criteria
    notice.value = ''
    if (!rows.value.some((row) => row.document_id === selectedId.value)) {
      selectedId.value = rows.value[0]?.document_id || ''
    }
  } catch (error) {
    if (sequence === querySequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

async function changePage(nextPage) {
  if (loading.value || filtersDirty.value || nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
  await searchFiles(false, nextPage)
}

async function saveDispatch() {
  if (isDownload.value || saving.value) return
  if (!activeFile.value) {
    showNotice('请先选择一个切割文件。', 'error')
    return
  }
  saving.value = true
  try {
    const item = await createCuttingDispatch({
      document_id: activeFile.value.document_id,
      machine_ip: dispatchForm.machineIp,
      material: dispatchForm.material || null,
      thickness: dispatchForm.thickness === '' ? null : Number(dispatchForm.thickness),
      quantity: Number(dispatchForm.quantity),
    })
    showNotice(`已保存派发任务 ${item.dispatch_id}，当前状态：${item.status_label}。`)
    await loadHistory()
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    saving.value = false
  }
}

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '--'
}

onMounted(async () => {
  loading.value = true
  try {
    const [statusData, schema] = await Promise.all([
      getCuttingStatus(),
      getConditionSchema('cutting_files'),
    ])
    systemStatus.value = statusData
    conditionSchema.value = schema
    await loadHistory()
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="dispatch-hero">
    <div>
      <span class="eyebrow">{{ isDownload ? 'FILE DOWNLOAD' : 'FILE PUSH' }}</span>
      <h2>{{ isDownload ? '文件下载' : '文件推送' }}</h2>
      <p>{{ isDownload ? '按条件检索文档，下载对应版本；管理、选择已下载的本地文件。' : '独立检索并选择待派发文档，填写目标机床和加工参数，查看派发记录。' }}</p>
    </div>
    <div class="dispatch-stage-badge">
      <span>{{ systemStatus?.safe_mode ? '安全联调模式' : '生产数据模式' }}</span>
      <strong>{{ systemStatus?.pdm?.label || '正在检查数据源' }} · {{ systemStatus?.file_source?.label || '--' }}</strong>
      <small>{{ systemStatus?.file_source?.message || '正在加载系统状态' }}</small>
    </div>
  </section>

  <div v-if="isDownload" class="dispatch-flow" aria-label="下载流程">
    <div class="active"><i>01</i><span><strong>组合条件查询</strong><small>字段 · 比较方式 · 条件值</small></span></div>
    <div><i>02</i><span><strong>下载对应版本</strong><small>文档名称 · 版本命名</small></span></div>
    <div><i>03</i><span><strong>选择本地文件</strong><small>名称 · 版本 · 保存位置</small></span></div>
  </div>
  <p v-else class="dispatch-push-scope" role="note">当前仅保存待派发参数，不会实际推送机床或回写 PDM；下载文件请进入“文件下载”子目录。本页选择与下载页独立，不会自动使用已下载文件。</p>

  <div v-if="notice" class="dispatch-notice" :class="{ error: noticeType === 'error' }" role="status">
    <span>{{ notice }}</span>
    <button type="button" @click="notice = ''">关闭</button>
  </div>

  <section class="panel dispatch-query-panel">
    <div class="panel-title">
      <div><span>01</span><h2>{{ isDownload ? '文件清单查询' : '待派发文档查询' }}</h2></div>
      <p>SQL 数据源：PDM_DesignDocumentInfo / PDM_DocumentFileInfo</p>
    </div>
    <form class="dispatch-query-dynamic" @submit.prevent="searchFiles(true)">
      <ConditionBuilder v-model="filters" :schema="conditionSchema" id-prefix="pdm-conditions" />
      <div class="dispatch-query-actions"><small>{{ filtersDirty && appliedFilters ? '条件已修改，请重新查询；旧结果暂不可操作。' : isDownload ? '先填写条件再查询。默认每页10条，下拉框只显示当前页结果。' : '先填写条件再查询。默认每页10条，选中文档后填写派发参数。' }}</small><button class="primary" type="submit" :disabled="loading || !conditionSchema">{{ loading ? '查询中…' : '查询文件' }}</button></div>
    </form>
  </section>

  <div class="dispatch-workspace" :class="{ 'download-workspace': isDownload }">
    <section class="panel dispatch-files-panel">
      <div class="panel-title">
        <div><span>02</span><h2>{{ isDownload ? '切割文件' : '选择待派发文档' }}</h2></div>
        <p>{{ systemStatus?.pdm?.label || '文件数据源' }} · 共 {{ total }} 条 · 当前第 {{ page }} 页</p>
      </div>
      <div class="dispatch-table-wrap">
        <table class="dispatch-table">
          <thead>
            <tr><th class="check-column">选择</th><th>文件名</th><th>工程 / 船号</th><th>版本</th><th>中间产品</th><th>操作人</th><th>操作时间</th><th>切割状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.document_id" :class="{ selected: !filtersDirty && selectedId === row.document_id }" @click="selectFile(row.document_id)">
              <td class="check-column"><input type="radio" name="cutting-file" :disabled="filtersDirty || loading" :checked="!filtersDirty && selectedId === row.document_id" :aria-label="`选择 ${row.document_no}`" @click.stop @change="selectFile(row.document_id)" /></td>
              <td class="file-name-cell"><strong>{{ row.document_no }}</strong><small>{{ row.normalized_path || row.path_error }}</small></td>
              <td>{{ row.ship_no || '--' }}<small class="dispatch-project-name">{{ row.project_name || '--' }}</small></td>
              <td>{{ row.version || row.physical_version_no || '--' }}</td><td>{{ row.pbs_info || '--' }}</td><td>{{ row.operator || '--' }}</td><td>{{ formatTime(row.operate_time) }}</td><td><span class="dispatch-status" :class="row.status">{{ row.status_label }}</span></td>
            </tr>
            <tr v-if="!loading && rows.length === 0"><td colspan="8" class="dispatch-empty-row">{{ appliedFilters ? '没有符合条件的文件，请调整条件重新查询。' : '请在上方添加条件并查询，不会自动加载全部文件。' }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="dispatch-pagination"><button type="button" :disabled="page <= 1 || loading || filtersDirty" @click="changePage(page - 1)">上一页</button><span>第 {{ page }} / {{ totalPages }} 页</span><button type="button" :disabled="page >= totalPages || loading || filtersDirty" @click="changePage(page + 1)">下一页</button></div>
      <div class="dispatch-path-preview">
        <span>当前文件相对路径</span>
        <code>{{ activeFile?.normalized_path || activeFile?.path_error || '选择文件后显示对应版本 FilePath' }}</code>
        <small v-if="physicalPath">文件服务器物理位置：{{ physicalPath }}</small>
        <small v-else>所有路径均由后端验证，禁止绝对路径和跨目录访问。</small>
      </div>
    </section>

    <aside v-if="!isDownload" class="panel dispatch-config-panel">
      <div class="panel-title"><div><span>03</span><h2>派发参数</h2></div></div>
      <div class="dispatch-selected-file">
        <span>当前选择</span>
        <strong>{{ activeFile?.document_no || '尚未选择文件' }}</strong>
        <small v-if="activeFile">{{ activeFile.path_available ? '相对路径有效' : activeFile.path_error }}</small>
      </div>
      <form class="dispatch-config" @submit.prevent="saveDispatch">
        <label><span>任务名称</span><input :value="activeFile?.document_no || ''" disabled /></label>
        <label><span>目标机床 IP</span><input v-model.trim="dispatchForm.machineIp" required placeholder="例如 10.1.133.196" /></label>
        <div class="dispatch-config-row">
          <label><span>材质</span><input v-model.trim="dispatchForm.material" placeholder="可选" /></label>
          <label><span>板厚（mm）</span><input v-model="dispatchForm.thickness" type="number" min="0" step="0.01" placeholder="0" /></label>
        </div>
        <label><span>加工数量</span><input v-model="dispatchForm.quantity" type="number" min="1" max="10000" step="1" required /></label>
        <div class="dispatch-task-id"><span>任务标识</span><code>提交时由系统生成唯一 ID</code></div>
        <div class="dispatch-warning"><strong>当前只保存待派发任务</strong><span>保存参数不会下载文件、调用魔盒或修改 PDM 状态。实际推送尚未接入。</span></div>
        <button class="primary dispatch-submit" type="submit" :disabled="!activeFile?.path_available || saving">{{ saving ? '保存中…' : '保存待派发任务' }}</button>
      </form>
    </aside>
  </div>

  <CuttingDownloadsPanel v-if="isDownload" :active-file="activeFile" :files="rows" :query-loading="loading || filtersDirty" :can-download="!!systemStatus?.can_download" @select-source="selectFile" />

  <section v-if="!isDownload" class="panel dispatch-history-panel">
    <div class="panel-title"><div><span>04</span><h2>派发记录</h2></div><p>当前记录任务参数和状态，不触发未配置的外部接口</p></div>
    <div v-if="histories.length" class="dispatch-table-wrap"><table class="dispatch-table history-table"><thead><tr><th>任务ID</th><th>文件名</th><th>机床IP</th><th>数量</th><th>状态</th><th>创建人</th><th>创建时间</th></tr></thead><tbody><tr v-for="item in histories" :key="item.dispatch_id"><td><code>{{ item.dispatch_id }}</code></td><td>{{ item.document_no }}</td><td>{{ item.machine_ip }}</td><td>{{ item.quantity }}</td><td><span class="dispatch-status">{{ item.status_label }}</span></td><td>{{ item.created_by_name }}</td><td>{{ formatTime(item.created_at) }}</td></tr></tbody></table></div>
    <div v-else class="dispatch-empty-history"><i>↗</i><strong>暂无派发记录</strong><span>选择文件和机床后可先保存一条待派发任务。</span></div>
  </section>
</template>

<style scoped>
.dispatch-hero { min-height:185px; display:flex; align-items:center; justify-content:space-between; gap:32px; }
.dispatch-query-dynamic { padding:22px; }
.dispatch-query-actions { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-top:14px; }
.dispatch-query-actions small { color:var(--muted); font-size:11px; }
.dispatch-project-name { display:block; color:var(--muted); font-size:10px; max-width:140px; overflow-wrap:anywhere; margin-top:5px; }
.dispatch-hero h2 { margin:8px 0 7px; font-size:34px; }
.dispatch-hero p { margin:0; color:var(--muted); }
.dispatch-stage-badge { min-width:310px; display:flex; flex-direction:column; padding:17px 20px; border:1px solid #e2c88f; border-right:4px solid #c78a22; background:rgba(255,250,238,.9); box-shadow:0 12px 30px rgba(62,48,20,.06); }
.dispatch-stage-badge span { color:#a86b10; font-size:10px; font-weight:700; letter-spacing:2px; }
.dispatch-stage-badge strong { margin:4px 0; color:#5f4928; font-size:13px; }
.dispatch-stage-badge small { color:#89785d; }
.dispatch-flow { display:grid; grid-template-columns:repeat(3,1fr); margin-bottom:22px; border:1px solid var(--line); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.045); }
.dispatch-push-scope { padding:14px 18px; margin:0 0 22px; background:#fff6e4; border-left:4px solid #c78a22; color:#7b581d; font-size:12px; line-height:1.7; }
.dispatch-flow > div { position:relative; min-height:82px; display:flex; align-items:center; gap:12px; padding:17px 20px; border-right:1px solid var(--line); }
.dispatch-flow > div:last-child { border-right:0; }
.dispatch-flow > div::after { content:'›'; position:absolute; right:-7px; z-index:1; color:#b7c5cd; background:#fff; font-size:24px; }
.dispatch-flow > div:last-child::after { display:none; }
.dispatch-flow i { width:32px; height:32px; display:grid; place-items:center; flex:0 0 auto; color:#718895; border:1px solid #c6d1d8; border-radius:50%; font-style:normal; font-size:10px; }
.dispatch-flow span { display:flex; flex-direction:column; }
.dispatch-flow strong { font-size:12px; }
.dispatch-flow small { margin-top:3px; color:var(--muted); font-size:10px; }
.dispatch-flow .active { background:#f1f8fa; }
.dispatch-flow .active i { color:#fff; border-color:var(--blue); background:var(--blue); }
.dispatch-notice { display:flex; align-items:center; gap:12px; margin-bottom:22px; padding:13px 16px; color:#1f6570; border-left:4px solid var(--cyan); background:#edf8f9; font-size:12px; }
.dispatch-notice.error { color:#8a342d; border-left-color:#c95a50; background:#fff0ee; }
.dispatch-notice button { min-height:30px; margin-left:auto; padding:0 8px; color:var(--blue); border:0; background:transparent; }
.dispatch-query { display:grid; grid-template-columns:1.1fr 1.1fr .8fr 1fr auto; gap:14px; align-items:end; padding:24px 26px 26px; }
.dispatch-query label > span,.dispatch-config label > span { display:block; margin-bottom:7px; color:#425767; font-size:11px; font-weight:700; }
.dispatch-workspace { display:grid; grid-template-columns:minmax(0,1.8fr) minmax(300px,.72fr); gap:22px; align-items:start; }
.dispatch-workspace.download-workspace { grid-template-columns:minmax(0,1fr); }
.dispatch-files-panel,.dispatch-config-panel { min-width:0; }
.dispatch-table-wrap { overflow-x:auto; }
.dispatch-table { width:100%; min-width:830px; border-collapse:collapse; }
.dispatch-table th,.dispatch-table td { padding:13px 14px; border-bottom:1px solid #e4eaee; text-align:left; font-size:11px; }
.dispatch-table th { color:#dcecf2; background:#0b4f76; font-size:10px; letter-spacing:.4px; }
.dispatch-table tbody tr { cursor:pointer; }
.dispatch-table tbody tr:hover { background:#f5fafb; }
.dispatch-table tbody tr.selected { background:#eef8fa; box-shadow:inset 3px 0 var(--cyan); }
.dispatch-table input[type='checkbox'] { width:15px; height:15px; }
.check-column { width:42px; text-align:center!important; }
.file-name-cell { max-width:290px; }
.file-name-cell strong,.file-name-cell small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.file-name-cell strong { color:var(--navy); font-size:11px; }
.file-name-cell small { max-width:270px; margin-top:4px; color:#84939d; font:9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.dispatch-status { display:inline-flex; padding:4px 8px; color:#8b5d12; border-radius:999px; background:#fff3d8; white-space:nowrap; font-size:10px; }
.dispatch-status.sent { color:#267048; background:#e8f7ef; }
.dispatch-status.failed { color:#9b3f37; background:#fdecea; }
.dispatch-empty-row { padding:32px!important; color:var(--muted); text-align:center!important; }
.dispatch-pagination { display:flex; align-items:center; justify-content:flex-end; gap:12px; padding:14px 22px 0; color:var(--muted); font-size:10px; }
.dispatch-pagination button { min-height:30px; padding:0 12px; color:var(--blue); border:1px solid #c8d7df; background:#fff; }
.dispatch-pagination button:disabled { color:#9cabb4; background:#f4f6f7; cursor:not-allowed; }
.dispatch-path-preview { display:flex; flex-direction:column; gap:7px; margin:18px 22px 22px; padding:15px 16px; border:1px solid #d8e3e8; background:#f7fafb; }
.dispatch-path-preview > span { color:#526a79; font-size:10px; font-weight:700; letter-spacing:1px; }
.dispatch-path-preview code { overflow-wrap:anywhere; color:#0d5d83; font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; }
.dispatch-path-preview small { color:var(--muted); font-size:10px; }
.dispatch-config-panel { position:sticky; top:18px; }
.dispatch-selected-file { display:flex; flex-direction:column; gap:4px; margin:20px 22px 0; padding:14px 15px; border-left:4px solid var(--cyan); background:#eef8fa; }
.dispatch-selected-file span { color:var(--muted); font-size:9px; letter-spacing:1px; }
.dispatch-selected-file strong { overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.dispatch-selected-file small { color:#a26917; font-size:9px; }
.dispatch-config { display:grid; gap:13px; padding:18px 22px 24px; }
.dispatch-config-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.dispatch-task-id { display:flex; flex-direction:column; gap:5px; padding:11px 12px; border:1px dashed #bdcbd5; background:#fafcfd; }
.dispatch-task-id span { color:#526a79; font-size:10px; font-weight:700; }
.dispatch-task-id code { color:#788a96; font-size:10px; }
.dispatch-warning { display:flex; flex-direction:column; gap:3px; padding:11px 12px; color:#7b581d; background:#fff6e4; }
.dispatch-warning strong { font-size:11px; }
.dispatch-warning span { font-size:9px; line-height:1.5; }
.dispatch-submit { width:100%; }
.dispatch-empty-history { min-height:160px; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:30px; color:var(--muted); text-align:center; }
.dispatch-empty-history i { width:38px; height:38px; display:grid; place-items:center; margin-bottom:10px; color:var(--blue); border:1px solid #a9cad7; border-radius:50%; background:#f1fafb; font-style:normal; font-size:18px; }
.dispatch-empty-history strong { color:#405866; font-size:13px; }
.dispatch-empty-history span { margin-top:5px; font-size:10px; }
.history-table { min-width:880px; }
.dispatch-history-panel { margin-top:22px; }
.history-table code { color:var(--blue); font-size:9px; }
@media (max-width:1000px) {
  .dispatch-hero { align-items:flex-start; flex-direction:column; justify-content:center; padding:28px 0; }
  .dispatch-stage-badge { width:100%; min-width:0; }
  .dispatch-flow { grid-template-columns:1fr 1fr; }
  .dispatch-flow > div:nth-child(2) { border-right:0; }
  .dispatch-flow > div:nth-child(-n+2) { border-bottom:1px solid var(--line); }
  .dispatch-workspace { grid-template-columns:1fr; }
  .dispatch-config-panel { position:static; }
  .dispatch-query { grid-template-columns:1fr 1fr; }
}
@media (max-width:560px) {
  .dispatch-flow,.dispatch-query,.dispatch-config-row { grid-template-columns:1fr; }
  .dispatch-flow > div { border-right:0; border-bottom:1px solid var(--line); }
  .dispatch-flow > div:last-child { border-bottom:0; }
  .dispatch-flow > div::after { display:none; }
  .dispatch-query button { width:100%; }
}
</style>
