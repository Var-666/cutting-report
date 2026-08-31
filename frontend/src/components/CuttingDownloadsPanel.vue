<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import ConditionBuilder from './ConditionBuilder.vue'
import { downloadCuttingFile, getConditionSchema, searchCuttingDownloads, saveCuttingDownload } from '../api/reports'

const props = defineProps({ activeFile: { type: Object, default: null }, files: { type: Array, default: () => [] }, canDownload: Boolean, queryLoading: Boolean })
const emit = defineEmits(['select-source'])
const items = ref([])
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const downloading = ref(false)
const savingId = ref('')
const selectedFile = ref(null)
const message = ref('')
const error = ref('')
const pageSize = 10
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const filters = ref({ logic: 'and', rules: [] })
const appliedFilters = ref({ logic: 'and', rules: [] })
const conditionSchema = ref(null)
const filtersDirty = computed(() => JSON.stringify(filters.value) !== JSON.stringify(appliedFilters.value))
let querySequence = 0
watch(filters, () => { selectedFile.value = null; message.value = '' }, { deep: true, flush: 'sync' })

function bytes(value) {
  if (value == null) return '--'
  if (value < 1024) return `${value} B`
  return value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`
}

async function refresh(nextPage = page.value) {
  if (!conditionSchema.value) return
  if (filtersDirty.value) nextPage = 1
  const criteria = JSON.parse(JSON.stringify(filters.value))
  const sequence = ++querySequence
  loading.value = true
  error.value = ''
  try {
    const data = await searchCuttingDownloads(criteria, { page: nextPage, pageSize })
    if (sequence !== querySequence || JSON.stringify(criteria) !== JSON.stringify(filters.value)) return
    items.value = data.items
    total.value = data.total
    page.value = nextPage
    appliedFilters.value = criteria
    const selected = items.value.find((item) => item.download_id === selectedFile.value?.download_id)
    selectedFile.value = selected?.available ? selected : null
  } catch (reason) {
    if (sequence === querySequence) error.value = reason.message
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

async function downloadSelected() {
  const file = props.activeFile
  if (!file?.path_available || !props.canDownload || props.queryLoading || downloading.value) return
  downloading.value = true
  error.value = ''
  message.value = `正在下载 ${file.document_no} 到系统本地，请勿重复提交…`
  try {
    const result = await downloadCuttingFile({ document_id: file.document_id, version_id: file.version_id || null, file_path: file.normalized_path })
    await refresh(1)
    const visible = items.value.find((item) => item.download_id === result.download_id)
    if (!filtersDirty.value && visible?.available) selectedFile.value = visible
    message.value = `${result.is_simulation ? '模拟文件' : '文件'}已保存：${result.filename}。${selectedFile.value?.download_id === result.download_id ? '已选中。' : '若列表未显示，请调整本地检索条件后查询。'}`
  } catch (reason) {
    message.value = ''
    await refresh(1)
    error.value = reason.message
  } finally {
    downloading.value = false
  }
}

function selectFile(item) {
  if (item.available && !loading.value && !filtersDirty.value) {
    selectedFile.value = item
    message.value = ''
  }
}

async function saveFile(item) {
  if (savingId.value || loading.value || filtersDirty.value) return
  savingId.value = item.download_id
  error.value = ''
  try {
    await saveCuttingDownload(item.download_id, item.filename)
    message.value = `已提交浏览器保存：${item.filename}。实际位置以浏览器下载设置为准。`
  } catch (reason) {
    error.value = reason.message
  } finally {
    savingId.value = ''
  }
}

onMounted(async () => {
  try {
    conditionSchema.value = await getConditionSchema('cutting_downloads')
    await refresh()
  } catch (reason) { error.value = reason.message }
})
</script>

<template>
  <section class="panel download-panel" aria-label="本地文件下载与选择">
    <div class="panel-title"><div><span>03</span><h2>已下载文件 · 选择文件</h2></div><p>保留文档名称与版本 · 不展示内容、不派发机床</p></div>
    <div class="download-toolbar">
      <div class="download-source-selector"><label for="download-source-file">选择需要下载的文件（当前查询页）</label><select id="download-source-file" :value="activeFile?.document_id || ''" :disabled="!files.length || queryLoading || downloading" @change="emit('select-source', $event.target.value)"><option value="" disabled>{{ queryLoading ? '请先完成文件查询' : '请选择文件' }}</option><option v-for="file in files" :key="file.document_id" :value="file.document_id" :disabled="!file.path_available">{{ file.document_no }} · 版本 {{ file.version || file.physical_version_no || '未标注' }}{{ file.path_available ? '' : ' · 路径不可用' }}</option></select><small>与上方清单联动；更换检索条件或翻页可选择其他文件。文件先保存到部署服务器。</small></div>
      <button class="primary" type="button" :disabled="!activeFile?.path_available || !canDownload || queryLoading || downloading" @click="downloadSelected">{{ downloading ? '正在下载到本地…' : '下载所选文件到本地' }}</button>
      <button type="button" :disabled="loading || filtersDirty || !conditionSchema" @click="refresh()">{{ loading ? '刷新中…' : '刷新记录' }}</button>
    </div>
    <details class="download-search">
      <summary>检索已下载文件（可增减条件）</summary>
      <form @submit.prevent="refresh(1)">
        <ConditionBuilder v-model="filters" :schema="conditionSchema" id-prefix="download-conditions" />
        <div class="download-search-actions"><small>{{ filtersDirty ? '条件已修改，请重新筛选；旧记录暂不可选择。' : '可按文件名、版本、下载日期、操作人、状态等筛选。' }}</small><button class="primary" type="submit" :disabled="loading || !conditionSchema">筛选本地记录</button></div>
      </form>
    </details>
    <p v-if="!canDownload" class="download-hint">真实下载需启用 PDM 数据源和 HTTP 文件服务；此前已保存的文件仍可选择和另存。</p>
    <p v-if="message" class="download-message" role="status">{{ message }}</p>
    <p v-if="error" class="download-error" role="alert">{{ error }}</p>
    <div class="download-table-wrap">
      <table class="download-table">
        <thead><tr><th>选择</th><th>本地文件名</th><th>版本 / 来源</th><th>大小</th><th>下载时间 / 操作人</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in items" :key="item.download_id" :class="{ selected: selectedFile?.download_id === item.download_id }" @click="selectFile(item)">
            <td><input type="radio" name="downloaded-file" :checked="selectedFile?.download_id === item.download_id" :disabled="!item.available || loading || filtersDirty" :aria-label="`选择本地文件 ${item.filename}`" @click.stop @change="selectFile(item)" /></td>
            <td class="download-name"><strong :title="item.filename">{{ item.filename }}</strong><small :title="item.relative_path">{{ item.relative_path }}</small><small v-if="item.error" class="download-row-error">{{ item.error }}</small></td>
            <td>{{ item.version }}<small>{{ item.is_simulation ? '模拟文件 · 不用于生产' : item.source_mode === 'http' ? 'FO 文件服务' : '本地目录' }}</small></td>
            <td>{{ bytes(item.size) }}</td>
            <td>{{ item.created_at.replace('T', ' ').slice(0, 19) }}<small>{{ item.created_by_name }}</small></td>
            <td><span class="download-badge" :class="{ failed: item.status === 'failed' || (item.status === 'downloaded' && !item.available), success: item.available }">{{ item.status_label }}</span></td>
            <td><div class="download-actions"><button type="button" :disabled="!item.available || !!savingId || loading || filtersDirty" @click.stop="saveFile(item)">{{ savingId === item.download_id ? '读取中…' : '另存到电脑' }}</button></div></td>
          </tr>
          <tr v-if="!items.length"><td colspan="7" class="download-empty">{{ loading ? '正在加载本地记录…' : appliedFilters.rules.length ? '没有符合条件的下载记录。' : '暂无下载记录。选择文件后点击“下载所选文件到本地”。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="download-pagination"><span>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</span><button :disabled="page <= 1 || loading || filtersDirty" @click="refresh(page - 1)">上一页</button><button :disabled="page >= pages || loading || filtersDirty" @click="refresh(page + 1)">下一页</button></div>
    <section v-if="selectedFile" class="download-selection" aria-label="已选择的本地文件">
      <div><strong>已选择：{{ selectedFile.filename }}</strong><button type="button" @click="selectedFile = null; message = ''">取消选择</button></div>
      <p>版本 {{ selectedFile.version }} · {{ bytes(selectedFile.size) }}{{ selectedFile.is_simulation ? ' · 模拟文件，不用于生产' : '' }}</p>
      <p>保存位置：<code>{{ selectedFile.storage_path }}</code></p>
      <small>当前仅选择本地文件，不展示文件内容，不自动生成派发任务或推送机床。</small>
    </section>
  </section>
</template>

<style scoped>
.download-panel { margin-top:22px; min-width:0; }
.download-search { margin:0 22px 18px; }
.download-search summary { cursor:pointer; color:var(--blue); font-size:12px; padding:10px 0; }
.download-search-actions { display:flex; gap:12px; align-items:center; justify-content:space-between; margin-top:12px; }
.download-search-actions small { font-size:11px; color:var(--muted); }
.download-toolbar { display:flex; gap:12px; align-items:center; padding:20px 22px 14px; flex-wrap:wrap; }
.download-toolbar > div { flex:1; min-width:240px; }
.download-toolbar strong { display:block; overflow-wrap:anywhere; color:var(--navy); font-size:13px; }
.download-toolbar small { display:block; margin-top:6px; color:var(--muted); font-size:11px; }
.download-source-selector { max-width:660px; }
.download-source-selector label { display:block; margin-bottom:7px; color:var(--navy); font-size:12px; font-weight:700; }
.download-source-selector select { width:100%; min-width:0; min-height:38px; font-size:12px; text-overflow:ellipsis; }
button { min-height:34px; padding:7px 12px; border:1px solid #c3d3dc; background:white; color:var(--blue); cursor:pointer; font-size:11px; }
button.primary { background:var(--blue); color:white; border-color:var(--blue); }
button:disabled { opacity:.55; cursor:not-allowed; }
.download-hint,.download-message,.download-error { margin:0 22px 14px; padding:11px 13px; font-size:12px; line-height:1.65; background:#eef7fa; color:#26596d; overflow-wrap:anywhere; }
.download-error { background:#fff0ee; color:#913a31; }
.download-table-wrap { overflow-x:auto; }
.download-table { border-collapse:collapse; width:100%; min-width:900px; font-size:11px; }
.download-table th { padding:12px 14px; text-align:left; color:#dfedf4; background:#0b4f76; white-space:nowrap; }
.download-table td { padding:13px 14px; border-bottom:1px solid #e4eaee; }
.download-table tr.selected { background:#eef8fa; box-shadow:inset 3px 0 var(--cyan); }
.download-table input[type='radio'] { width:16px; height:16px; }
.download-table td small { display:block; margin-top:4px; color:var(--muted); font-size:10px; }
.download-name { max-width:340px; }
.download-name strong,.download-name small { display:block; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.download-table td small.download-row-error,.download-row-error { color:#963d30; }
.download-badge { padding:5px 8px; border-radius:20px; color:#8b5d12; background:#fff3d8; white-space:nowrap; }
.download-badge.success { color:#267048; background:#e8f7ef; }
.download-badge.failed { color:#963d30; background:#fff0ee; }
.download-actions { display:flex; gap:7px; white-space:nowrap; }
.download-empty { text-align:center; padding:30px!important; color:var(--muted); }
.download-pagination { display:flex; justify-content:flex-end; gap:12px; align-items:center; padding:15px 22px; font-size:11px; color:var(--muted); }
.download-selection { margin:5px 22px 22px; padding:18px; background:#f5f9fc; border:1px solid #d4e1e9; overflow-wrap:anywhere; }
.download-selection > div { display:flex; justify-content:space-between; gap:15px; align-items:center; }
.download-selection strong { color:var(--navy); font-size:13px; }
.download-selection p,.download-selection small { color:var(--muted); font-size:11px; }
@media(max-width:700px) { .download-toolbar { align-items:stretch; } .download-toolbar > div { width:100%; } .download-selection { margin:5px 12px 12px; padding:12px; } }
</style>
