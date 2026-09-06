<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import ConditionBuilder from './ConditionBuilder.vue'
import SortBuilder from './SortBuilder.vue'
import { canChooseSaveLocation, getConditionSchema, searchCuttingDownloads, saveCuttingDownload } from '../api/reports'
const items = ref([])
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const savingId = ref('')
const selectedFile = ref(null)
const message = ref('')
const error = ref('')
const saveDialog = ref(null)
const pendingSave = ref(null)
const pageSize = 10
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const filters = ref({ logic: 'and', rules: [] })
const sort = ref([])
const appliedSort = ref([])
const appliedFilters = ref({ logic: 'and', rules: [] })
const conditionSchema = ref(null)
const filtersDirty = computed(() => JSON.stringify(filters.value) !== JSON.stringify(appliedFilters.value) || JSON.stringify(sort.value) !== JSON.stringify(appliedSort.value))
let querySequence = 0
watch([filters, sort], () => { selectedFile.value = null; message.value = '' }, { deep: true, flush: 'sync' })

function bytes(value) {
  if (value == null) return '--'
  if (value < 1024) return `${value} B`
  return value < 1024 ** 2 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1024 ** 2).toFixed(1)} MB`
}

async function refresh(nextPage = page.value) {
  if (!conditionSchema.value) return
  if (filtersDirty.value) nextPage = 1
  const criteria = JSON.parse(JSON.stringify(filters.value))
  const ordering = JSON.parse(JSON.stringify(sort.value))
  const sequence = ++querySequence
  loading.value = true
  error.value = ''
  try {
    const data = await searchCuttingDownloads(criteria, { page: nextPage, pageSize, sort: ordering })
    if (sequence !== querySequence || JSON.stringify(criteria) !== JSON.stringify(filters.value) || JSON.stringify(ordering) !== JSON.stringify(sort.value)) return
    items.value = data.items
    total.value = data.total
    page.value = nextPage
    appliedFilters.value = criteria
    appliedSort.value = ordering
    const selected = items.value.find((item) => item.download_id === selectedFile.value?.download_id)
    selectedFile.value = selected?.available ? selected : null
  } catch (reason) {
    if (sequence === querySequence) error.value = reason.message
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

// Background updates must not submit an unfinished local-search draft.
defineExpose({ refresh: () => filtersDirty.value ? Promise.resolve() : refresh(1) })

function selectFile(item) {
  if (item.available && !loading.value && !filtersDirty.value) {
    selectedFile.value = item
    message.value = ''
  }
}

async function saveFile(item, browserDownload = false) {
  if (savingId.value || loading.value || filtersDirty.value) return
  if (!browserDownload && !canChooseSaveLocation()) {
    pendingSave.value = item
    saveDialog.value.showModal()
    return
  }
  savingId.value = item.download_id
  error.value = ''
  message.value = ''
  try {
    const result = await saveCuttingDownload(item.download_id, item.filename, { browserDownload })
    if (result.mode === 'picker') message.value = `已保存到所选位置：${result.filename}`
    if (result.mode === 'browser') message.value = `已交由浏览器下载：${item.filename}。开启“下载前询问保存位置”后可选择路径；否则保存到浏览器默认下载目录。`
  } catch (reason) {
    error.value = reason.message
  } finally {
    savingId.value = ''
  }
}

function continueBrowserSave() {
  const item = pendingSave.value
  saveDialog.value.close()
  pendingSave.value = null
  if (item) saveFile(item, true)
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
    <dialog ref="saveDialog" class="save-location-dialog" aria-labelledby="save-location-title" @close="pendingSave = null">
      <h3 id="save-location-title">选择电脑保存位置</h3>
      <p>当前浏览器或内网 HTTP 页面不支持网页直接打开路径选择窗口。</p>
      <p>请在 Chrome / Edge 的<strong>设置 → 下载</strong>中开启<strong>“下载前询问每个文件的保存位置”</strong>（名称因浏览器而异），再继续下载，即可选择文件夹和文件名。</p>
      <p class="save-location-note">未开启此选项时，文件会存入浏览器默认下载目录，不会弹出路径窗口。</p>
      <div><button type="button" autofocus @click="saveDialog.close()">取消</button><button type="button" class="primary" @click="continueBrowserSave">继续浏览器下载</button></div>
    </dialog>
    <div class="panel-title"><div><h2>已下载文件</h2></div><button type="button" :disabled="loading || filtersDirty || !conditionSchema" @click="refresh()">{{ loading ? '刷新中…' : '刷新记录' }}</button></div>
    <details class="download-search">
      <summary>检索已下载文件（可增减条件）</summary>
      <form @submit.prevent="refresh(1)">
        <ConditionBuilder v-model="filters" :schema="conditionSchema" id-prefix="download-conditions" />
        <SortBuilder v-model="sort" :schema="conditionSchema" default-label="下载时间由近到远" />
        <div class="download-search-actions"><small>{{ filtersDirty ? '条件已修改，请重新筛选；旧记录暂不可选择。' : '可按文件名、版本、下载日期、操作人、状态等筛选。' }}</small><button class="primary" type="submit" :disabled="loading || !conditionSchema">筛选本地记录</button></div>
      </form>
    </details>
    <p v-if="message" class="download-message" role="status">{{ message }}</p>
    <p v-if="error" class="download-error" role="alert">{{ error }}</p>
    <p class="download-hint">“另存到电脑”可在支持的浏览器中选择保存路径；内网 HTTP 页面请先在浏览器下载设置中开启“下载前询问保存位置”。</p>
    <div class="download-table-wrap">
      <table v-content-table class="download-table">
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
          <tr v-if="!items.length"><td colspan="7" class="download-empty">{{ loading ? '正在加载本地记录…' : appliedFilters.rules.length ? '没有符合条件的下载记录。' : '暂无下载记录。请在上方查询结果表格中点击下载。' }}</td></tr>
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
.save-location-dialog { width:min(520px,calc(100vw - 32px)); padding:24px; color:var(--navy); border:1px solid #c3d3dc; box-shadow:var(--shadow); }
.save-location-dialog::backdrop { background:rgba(20,42,56,.45); }
.save-location-dialog h3 { margin:0 0 14px; }
.save-location-dialog p { font-size:13px; line-height:1.8; }
.save-location-dialog .save-location-note { color:var(--muted); font-size:12px; }
.save-location-dialog > div { display:flex; justify-content:flex-end; gap:12px; margin-top:20px; }
.download-search { margin:0 22px 18px; }
.download-search summary { cursor:pointer; color:var(--blue); font-size:12px; padding:10px 0; }
.download-search-actions { display:flex; gap:12px; align-items:center; justify-content:space-between; margin-top:12px; }
.download-search-actions small { font-size:11px; color:var(--muted); }
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
@media(max-width:700px) { .download-selection { margin:5px 12px 12px; padding:12px; } }
</style>
