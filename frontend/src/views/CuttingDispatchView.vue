<script setup>
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import CuttingDownloadsPanel from '../components/CuttingDownloadsPanel.vue'
import ConditionBuilder from '../components/ConditionBuilder.vue'
import SortBuilder from '../components/SortBuilder.vue'
import {
  cancelCuttingDispatch,
  createCuttingDispatch,
  executeCuttingDispatch,
  getCuttingStatus,
  listCuttingDispatches,
  listCuttingDownloads,
  listCuttingMachines,
  getConditionSchema,
  searchCuttingFiles,
  clearCuttingFilePages,
  downloadCuttingFile,
} from '../api/reports'

const props = defineProps({ mode: { type: String, required: true, validator: (value) => ['download', 'push'].includes(value) } })
const isDownload = computed(() => props.mode === 'download')
const filters = ref({ logic: 'and', rules: [] })
const sort = ref([])
const appliedSort = ref([])
const conditionSchema = ref(null)
const appliedFilters = ref(null)
let querySequence = 0
const dispatchForm = reactive({ machineIp: '', material: '', thickness: '', quantity: 1 })
const systemStatus = ref(null)
const rows = ref([])
const histories = ref([])
const machines = ref([])
const pushFiles = ref([])
const selectedPushFileId = ref('')
const downloadsPanel = ref(null)
const downloadingId = ref('')
const downloadedRows = reactive({})
const loading = ref(false)
const saving = ref(false)
const operatingId = ref('')
const notice = ref('')
const noticeType = ref('info')
const page = ref(1)
const requestedPage = ref(1)
const pageSize = 10
const total = ref(0)

const filtersDirty = computed(() => !appliedFilters.value || JSON.stringify(filters.value) !== JSON.stringify(appliedFilters.value) || JSON.stringify(sort.value) !== JSON.stringify(appliedSort.value))
const activeFile = computed(() => pushFiles.value.find((row) => row.download_id === selectedPushFileId.value) || null)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
watch([filters, sort], () => {
  querySequence += 1
  clearCuttingFilePages()
  loading.value = false
}, { deep: true })
onBeforeUnmount(() => { querySequence += 1; clearCuttingFilePages() })

function showNotice(message, type = 'info') {
  notice.value = message
  noticeType.value = type
}

function fileKey(file) {
  return JSON.stringify([file.document_id, file.version_id, file.normalized_path])
}

async function downloadFile(file) {
  if (!isDownload.value || downloadingId.value || filtersDirty.value || loading.value || !file.path_available || !systemStatus.value?.can_download) return
  const key = fileKey(file)
  downloadingId.value = key
  showNotice(`正在下载 ${file.document_no} 到系统本地…`)
  try {
    const result = await downloadCuttingFile({ document_id: file.document_id, version_id: file.version_id || null, file_path: file.normalized_path })
    downloadedRows[key] = true
    showNotice(`${result.filename} 已保存到系统本地，可在“已下载文件”列表中另存到电脑。`)
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    downloadingId.value = ''
    await downloadsPanel.value?.refresh(1)
  }
}

async function loadHistory() {
  if (isDownload.value) return
  const data = await listCuttingDispatches({ page: 1, pageSize: 10 })
  histories.value = data.items
}

async function loadMachines() {
  if (isDownload.value || !systemStatus.value?.magic_box?.configured) return
  try {
    const data = await listCuttingMachines()
    machines.value = data.items || []
    if (!dispatchForm.machineIp && machines.value.length) {
      dispatchForm.machineIp = machines.value[0].machine_ip
    }
  } catch (error) {
    machines.value = (systemStatus.value?.magic_box?.machine_ips || []).map((machineIp) => ({
      machine_ip: machineIp,
      online: false,
      configured: true,
    }))
    if (!dispatchForm.machineIp && machines.value.length) dispatchForm.machineIp = machines.value[0].machine_ip
    showNotice(`机床在线状态读取失败：${error.message}。仍可选择 SYS_CONFIG 中配置的机床。`, 'error')
  }
}

async function loadPushFiles() {
  if (isDownload.value) return
  const data = await listCuttingDownloads({ page: 1, pageSize: 100 })
  pushFiles.value = (data.items || []).filter((item) => item.available && !item.is_simulation)
  if (!pushFiles.value.some((item) => item.download_id === selectedPushFileId.value)) {
    selectedPushFileId.value = pushFiles.value[0]?.download_id || ''
  }
}

async function searchFiles(resetPage = true, targetPage = page.value) {
  if (!filters.value.rules.length) {
    showNotice('请添加至少一条检索条件后查询，不会加载全部文件。', 'error')
    return
  }
  const nextPage = resetPage || filtersDirty.value ? 1 : targetPage
  const criteria = JSON.parse(JSON.stringify(filters.value))
  const ordering = JSON.parse(JSON.stringify(sort.value))
  const sequence = ++querySequence
  loading.value = true
  requestedPage.value = nextPage
  try {
    const data = await searchCuttingFiles(criteria, { page: nextPage, pageSize, sort: ordering, force: resetPage })
    if (sequence !== querySequence || JSON.stringify(criteria) !== JSON.stringify(filters.value) || JSON.stringify(ordering) !== JSON.stringify(sort.value)) return
    rows.value = data.items
    total.value = data.total
    page.value = nextPage
    appliedFilters.value = criteria
    appliedSort.value = ordering
    notice.value = ''
    if (nextPage < Math.ceil(data.total / pageSize)) {
      // One adjacent page only: never walk the entire PDM result set.
      searchCuttingFiles(criteria, { page: nextPage + 1, pageSize, sort: ordering }).catch(() => {})
    }
  } catch (error) {
    if (sequence === querySequence && error.name !== 'AbortError') showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

async function changePage(nextPage) {
  if (loading.value || filtersDirty.value || nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
  await searchFiles(false, nextPage)
}

async function saveDispatch(executeNow = false) {
  if (isDownload.value || saving.value) return
  if (!activeFile.value) {
    showNotice('请先选择一个切割文件。', 'error')
    return
  }
  if (executeNow && !window.confirm(`确认将文件“${activeFile.value.filename}”推送到机床 ${dispatchForm.machineIp}？\n任务名称将使用该文件名。`)) return
  saving.value = true
  try {
    const item = await createCuttingDispatch({
      download_id: activeFile.value.download_id,
      machine_ip: dispatchForm.machineIp,
      material: dispatchForm.material || null,
      thickness: dispatchForm.thickness === '' ? null : Number(dispatchForm.thickness),
      quantity: Number(dispatchForm.quantity),
    })
    if (executeNow) {
      operatingId.value = item.dispatch_id
      const completed = await executeCuttingDispatch(item.dispatch_id)
      showNotice(`文件已上传魔盒并指派到 ${completed.machine_ip}，任务名称使用实际上传文件名。`)
    } else {
      showNotice(`已保存派发任务 ${item.dispatch_id}，当前状态：${item.status_label}。`)
    }
    await loadHistory()
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    saving.value = false
    operatingId.value = ''
  }
}

async function executeSaved(item) {
  if (operatingId.value) return
  if (!window.confirm(`确认执行任务 ${item.dispatch_id}？\n文件：${item.document_no}\n机床：${item.machine_ip}`)) return
  operatingId.value = item.dispatch_id
  try {
    const completed = await executeCuttingDispatch(item.dispatch_id)
    showNotice(`推送完成，魔盒任务 UUID：${completed.remote_task_id}`)
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    operatingId.value = ''
    await loadHistory()
  }
}

async function cancelSaved(item) {
  if (operatingId.value || !window.confirm(`确认取消任务 ${item.dispatch_id} 的机床指派？`)) return
  operatingId.value = item.dispatch_id
  try {
    await cancelCuttingDispatch(item.dispatch_id)
    showNotice('已取消魔盒机床指派。')
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    operatingId.value = ''
    await loadHistory()
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
      isDownload.value ? getConditionSchema('cutting_files') : Promise.resolve(null),
    ])
    systemStatus.value = statusData
    conditionSchema.value = schema
    await loadPushFiles()
    await loadMachines()
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
      <p>{{ isDownload ? '按条件检索文档，下载对应版本；管理、选择已下载的本地文件。' : '选择已下载文件，填写目标机床和加工参数，确认后推送。' }}</p>
    </div>
    <div class="dispatch-stage-badge">
      <span>{{ systemStatus?.safe_mode ? '安全联调模式' : '生产数据模式' }}</span>
      <strong>{{ systemStatus?.pdm?.label || '正在检查数据源' }} · {{ systemStatus?.file_source?.label || '--' }}</strong>
      <small>{{ systemStatus?.file_source?.message || '正在加载系统状态' }}</small>
    </div>
  </section>

  <div v-if="isDownload" class="dispatch-flow" aria-label="下载流程">
    <div class="active"><i>01</i><span><strong>组合条件查询</strong><small>字段 · 比较方式 · 条件值</small></span></div>
    <div><i>02</i><span><strong>表格内下载</strong><small>按行下载 · 已下载列表保留记录</small></span></div>
  </div>
  <p v-else class="dispatch-push-scope" role="note">确认推送后，系统会校验并复用已下载的准确版本，必要时先从 FO 下载，再上传魔盒并指派机床。每次真实推送都需要人工确认，不会按查询结果批量执行。</p>

  <div v-if="notice" class="dispatch-notice" :class="{ error: noticeType === 'error' }" role="status">
    <span>{{ notice }}</span>
    <button type="button" @click="notice = ''">关闭</button>
  </div>

  <section v-if="isDownload" class="panel dispatch-query-panel">
    <div class="panel-title">
      <div><span>01</span><h2>{{ isDownload ? '文件清单查询' : '待派发文档查询' }}</h2></div>
      <p>SQL 主表：PDM_DesignDocumentInfo / PDM_NCDocumentCatalog / PDM_DocumentFileInfo</p>
    </div>
    <form class="dispatch-query-dynamic" @submit.prevent="searchFiles(true)">
      <ConditionBuilder v-model="filters" :schema="conditionSchema" id-prefix="pdm-conditions" />
      <SortBuilder v-model="sort" :schema="conditionSchema" />
      <div class="dispatch-query-actions"><small>{{ filtersDirty && appliedFilters ? '条件或排序已修改，请重新查询；旧结果暂不可操作。' : '每页10条，可在结果行直接下载；翻页短期复用结果，重新查询可刷新。' }}</small><button class="primary" type="submit" :disabled="loading || !conditionSchema">{{ loading ? '查询中…' : '查询文件' }}</button></div>
    </form>
  </section>

  <div class="dispatch-workspace" :class="{ 'download-workspace': isDownload, 'push-workspace': !isDownload }">
    <section v-if="isDownload" class="panel dispatch-files-panel">
      <div class="panel-title">
        <div><span>02</span><h2>{{ isDownload ? '切割文件' : '选择待派发文档' }}</h2></div>
        <p>{{ systemStatus?.pdm?.label || '文件数据源' }} · 共 {{ total }} 条 · 当前第 {{ page }} 页</p>
      </div>
      <div class="dispatch-table-wrap" :aria-busy="loading">
        <div v-if="loading" class="dispatch-loading" role="status">正在加载第 {{ requestedPage }} 页…</div>
        <table v-content-table class="dispatch-table" aria-label="文件查询结果">
          <thead>
            <tr><th>文件名</th><th>工程 / 船号</th><th>版本</th><th>中间产品</th><th>操作人</th><th>操作时间</th><th>切割状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="row.document_id">
              <td class="file-name-cell"><strong>{{ row.document_no }}</strong><small :title="row.normalized_path || row.path_error">{{ row.normalized_path || row.path_error }}</small></td>
              <td>{{ row.ship_no || '--' }}<small class="dispatch-project-name">{{ row.project_name || '--' }}</small></td>
              <td>{{ row.version || row.physical_version_no || '--' }}</td><td>{{ row.pbs_info || '--' }}</td><td>{{ row.operator || '--' }}</td><td>{{ formatTime(row.operate_time) }}</td><td><span class="dispatch-status" :class="row.status">{{ row.status_label }}</span></td>
              <td class="file-download-cell"><button type="button" :disabled="loading || filtersDirty || !!downloadingId || !row.path_available || !systemStatus?.can_download" :aria-label="`下载 ${row.document_no}`" @click="downloadFile(row)">{{ downloadingId === fileKey(row) ? '下载中…' : downloadedRows[fileKey(row)] ? '再次下载' : '下载到本地' }}</button><small v-if="downloadedRows[fileKey(row)]">本次已下载</small><small v-else-if="!row.path_available">{{ row.path_error || '路径不可用' }}</small></td>
            </tr>
            <tr v-if="!loading && rows.length === 0"><td colspan="8" class="dispatch-empty-row">{{ appliedFilters ? '没有符合条件的文件，请调整条件重新查询。' : '请在上方添加条件并查询，不会自动加载全部文件。' }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="dispatch-pagination"><button type="button" :disabled="page <= 1 || loading || filtersDirty" @click="changePage(page - 1)">上一页</button><span>第 {{ page }} / {{ totalPages }} 页</span><button type="button" :disabled="page >= totalPages || loading || filtersDirty" @click="changePage(page + 1)">下一页</button></div>
      <p class="dispatch-download-hint">文件先保存到部署服务器，在下方“已下载文件”中可另存到电脑。下载时会重新校验文件路径与版本。{{ systemStatus && !systemStatus.can_download ? '当前文件来源未启用，暂不可下载。' : '' }}</p>
    </section>

    <aside v-if="!isDownload" class="panel dispatch-config-panel">
      <div class="panel-title"><div><span>01</span><h2>派发参数</h2></div><p>仅使用已下载且通过校验的本地文件</p></div>
      <div class="dispatch-selected-file">
        <span>当前选择</span>
        <strong>{{ activeFile?.filename || '尚未选择文件' }}</strong>
        <small v-if="activeFile">版本 {{ activeFile.version || '--' }} · {{ activeFile.storage_path }}</small>
      </div>
      <form class="dispatch-config" @submit.prevent="saveDispatch(false)">
        <label><span>选择已下载文件</span><select v-model="selectedPushFileId" required><option value="" disabled>请先到“文件下载”页下载文件</option><option v-for="file in pushFiles" :key="file.download_id" :value="file.download_id">{{ file.filename }}</option></select><small>这里只列出系统本地已下载、可用且非模拟的文件。</small></label>
        <label><span>任务名称（自动）</span><input :value="activeFile?.filename || ''" disabled /><small>taskName 与实际上传文件名一致，包含扩展名。</small></label>
        <label><span>目标机床 IP</span><select v-model="dispatchForm.machineIp" required><option value="" disabled>请选择 SYS_CONFIG 中的机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machine.server_name ? `${machine.server_name} · ` : '' }}{{ machine.machine_ip }}（{{ machine.online ? '在线' : '未从列表确认在线' }}）</option></select></label>
        <div class="dispatch-config-row">
          <label><span>材质</span><input v-model.trim="dispatchForm.material" placeholder="可选" /></label>
          <label><span>板厚（mm）</span><input v-model="dispatchForm.thickness" type="number" min="0" step="0.01" placeholder="0" /></label>
        </div>
        <label><span>加工数量</span><input v-model="dispatchForm.quantity" type="number" min="1" max="10000" step="1" required /></label>
        <div class="dispatch-task-id"><span>任务标识</span><code>提交时由系统生成唯一 ID</code></div>
        <div class="dispatch-warning"><strong>安全推送</strong><span>上传、指派分阶段记录。如上传超时导致结果不明，系统会停止自动重试，避免重复任务。</span></div>
        <div class="dispatch-submit-row"><button type="submit" :disabled="!activeFile?.available || saving">保存草稿</button><button class="primary" type="button" :disabled="!activeFile?.available || !dispatchForm.machineIp || !systemStatus?.can_push || saving" @click="saveDispatch(true)">{{ saving ? '处理中…' : '确认并推送' }}</button></div>
      </form>
    </aside>
  </div>

  <CuttingDownloadsPanel v-if="isDownload" ref="downloadsPanel" />

  <section v-if="!isDownload" class="panel dispatch-history-panel">
    <div class="panel-title"><div><span>02</span><h2>派发记录</h2></div><p>文件准备 → 魔盒上传 → 机床指派，各阶段独立记录</p></div>
    <div v-if="histories.length" class="dispatch-table-wrap"><table v-content-table class="dispatch-table history-table"><thead><tr><th>任务标识</th><th>文件名</th><th>机床IP</th><th>阶段 / 状态</th><th>魔盒UUID</th><th>失败原因</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in histories" :key="item.dispatch_id"><td><code>{{ item.task_identifier || item.dispatch_id }}</code></td><td>{{ item.document_no }}<small class="dispatch-project-name">数量 {{ item.quantity }}</small></td><td>{{ item.machine_ip }}</td><td><span class="dispatch-status" :class="item.status">{{ item.status_label }}</span><small class="dispatch-project-name">{{ item.stage_label }}</small></td><td><code>{{ item.remote_task_id || '--' }}</code></td><td class="history-error">{{ item.last_error || '--' }}</td><td>{{ formatTime(item.created_at) }}</td><td><div class="history-actions"><button v-if="item.can_execute" type="button" :disabled="!!operatingId" @click="executeSaved(item)">{{ item.remote_task_id ? '重试指派' : '执行推送' }}</button><button v-if="item.can_cancel" type="button" class="danger" :disabled="!!operatingId" @click="cancelSaved(item)">取消指派</button><span v-if="!item.can_execute && !item.can_cancel">--</span></div></td></tr></tbody></table></div>
    <div v-else class="dispatch-empty-history"><i>↗</i><strong>暂无派发记录</strong><span>选择文件和机床后可保存草稿，或明确确认后直接推送。</span></div>
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
.dispatch-flow { display:grid; grid-template-columns:repeat(2,1fr); margin-bottom:22px; border:1px solid var(--line); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.045); }
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
.dispatch-workspace.push-workspace { grid-template-columns:minmax(0,1fr); }
.dispatch-files-panel,.dispatch-config-panel { min-width:0; }
.dispatch-table-wrap { overflow-x:auto; position:relative; min-height:75px; }
.dispatch-loading { position:absolute; z-index:2; inset:0; display:flex; align-items:flex-start; justify-content:center; padding-top:48px; background:rgba(247,251,253,.8); color:var(--blue); font-size:12px; }
.file-download-cell { width:122px; }.file-download-cell button { min-height:32px; padding:6px 10px; border:1px solid #bdd2dd; background:#fff; color:var(--blue); font-size:11px; white-space:nowrap; cursor:pointer; }.file-download-cell button:disabled { opacity:.55; cursor:not-allowed; }.file-download-cell small { display:block; margin-top:4px; color:var(--muted); font-size:10px; }
.dispatch-download-hint { margin:16px 22px; font-size:11px; color:var(--muted); }
.dispatch-table { width:100%; min-width:830px; border-collapse:collapse; }
.dispatch-table th,.dispatch-table td { padding:13px 14px; border-bottom:1px solid #e4eaee; text-align:left; font-size:11px; }
.dispatch-table th { color:#dcecf2; background:#0b4f76; font-size:10px; letter-spacing:.4px; }
.dispatch-table tbody tr { cursor:default; }
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
.push-workspace .dispatch-config-panel { position:static; }
.push-workspace .dispatch-config { grid-template-columns:repeat(2,minmax(0,1fr)); column-gap:20px; }
.push-workspace .dispatch-selected-file,.push-workspace .dispatch-warning,.push-workspace .dispatch-task-id,.push-workspace .dispatch-submit-row { grid-column:1/-1; }
.dispatch-selected-file { display:flex; flex-direction:column; gap:4px; margin:20px 22px 0; padding:14px 15px; border-left:4px solid var(--cyan); background:#eef8fa; }
.dispatch-selected-file span { color:var(--muted); font-size:9px; letter-spacing:1px; }
.dispatch-selected-file strong { overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.dispatch-selected-file small { color:#a26917; font-size:9px; }
.dispatch-config { display:grid; gap:13px; padding:18px 22px 24px; }
.dispatch-config label > small { display:block; margin-top:5px; color:var(--muted); font-size:9px; line-height:1.5; }
.dispatch-config-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.dispatch-task-id { display:flex; flex-direction:column; gap:5px; padding:11px 12px; border:1px dashed #bdcbd5; background:#fafcfd; }
.dispatch-task-id span { color:#526a79; font-size:10px; font-weight:700; }
.dispatch-task-id code { color:#788a96; font-size:10px; }
.dispatch-warning { display:flex; flex-direction:column; gap:3px; padding:11px 12px; color:#7b581d; background:#fff6e4; }
.dispatch-warning strong { font-size:11px; }
.dispatch-warning span { font-size:9px; line-height:1.5; }
.dispatch-submit-row { display:grid; grid-template-columns:.8fr 1.2fr; gap:9px; }
.dispatch-submit-row button { width:100%; }
.dispatch-empty-history { min-height:160px; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:30px; color:var(--muted); text-align:center; }
.dispatch-empty-history i { width:38px; height:38px; display:grid; place-items:center; margin-bottom:10px; color:var(--blue); border:1px solid #a9cad7; border-radius:50%; background:#f1fafb; font-style:normal; font-size:18px; }
.dispatch-empty-history strong { color:#405866; font-size:13px; }
.dispatch-empty-history span { margin-top:5px; font-size:10px; }
.history-table { min-width:1180px; }
.dispatch-history-panel { margin-top:22px; }
.history-table code { color:var(--blue); font-size:9px; }
.history-error { max-width:220px; color:#8a4c45; overflow-wrap:anywhere; }
.history-actions { display:flex; align-items:center; gap:6px; }
.history-actions button { min-height:30px; padding:0 9px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; white-space:nowrap; font-size:10px; }
.history-actions button.danger { color:#9b3f37; border-color:#e3b8b4; }
@media (max-width:1000px) {
  .dispatch-hero { align-items:flex-start; flex-direction:column; justify-content:center; padding:28px 0; }
  .dispatch-stage-badge { width:100%; min-width:0; }
  .dispatch-flow { grid-template-columns:1fr 1fr; }
  .dispatch-flow > div:nth-child(2) { border-right:0; }
  .dispatch-flow > div:nth-child(-n+2) { border-bottom:1px solid var(--line); }
  .dispatch-workspace { grid-template-columns:1fr; }
  .dispatch-config-panel { position:static; }
  .push-workspace .dispatch-config { grid-template-columns:1fr; }
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
