<script setup>
import { computed, onMounted, ref } from 'vue'
import ConditionBuilder from './ConditionBuilder.vue'
import SortBuilder from './SortBuilder.vue'
import { getConditionSchema, searchCuttingDownloads } from '../api/reports'
const items = ref([])
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const error = ref('')
const pageSize = 10
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const filters = ref({ logic: 'and', rules: [] })
const sort = ref([])
const appliedSort = ref([])
const appliedFilters = ref({ logic: 'and', rules: [] })
const conditionSchema = ref(null)
const filtersDirty = computed(() => JSON.stringify(filters.value) !== JSON.stringify(appliedFilters.value) || JSON.stringify(sort.value) !== JSON.stringify(appliedSort.value))
let querySequence = 0
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
  } catch (reason) {
    if (sequence === querySequence) error.value = reason.message
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

// Background updates must not submit an unfinished local-search draft.
defineExpose({ refresh: () => filtersDirty.value ? Promise.resolve() : refresh(1) })

onMounted(async () => {
  try {
    conditionSchema.value = await getConditionSchema('cutting_downloads')
    await refresh()
  } catch (reason) { error.value = reason.message }
})
</script>

<template>
  <section class="panel download-panel" aria-label="系统已保存文件记录">
    <div class="panel-title"><div><h2>系统已保存文件</h2></div><button type="button" :disabled="loading || filtersDirty || !conditionSchema" @click="refresh()">{{ loading ? '刷新中…' : '刷新记录' }}</button></div>
    <details class="download-search">
      <summary>检索系统已保存文件（可增减条件）</summary>
      <form @submit.prevent="refresh(1)">
        <ConditionBuilder v-model="filters" :schema="conditionSchema" id-prefix="download-conditions" />
        <SortBuilder v-model="sort" :schema="conditionSchema" default-label="保存时间由近到远" />
        <div class="download-search-actions"><small>{{ filtersDirty ? '条件已修改，请重新筛选。' : '可按文件名、版本、保存日期、操作人、状态等筛选。' }}</small><button class="primary" type="submit" :disabled="loading || !conditionSchema">筛选保存记录</button></div>
      </form>
    </details>
    <p v-if="error" class="download-error" role="alert">{{ error }}</p>
    <p class="download-hint">这些文件保存在系统服务器中，可在“文件推送”页面选择并推送到魔盒。</p>
    <div class="download-table-wrap">
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
          <tr v-if="!items.length"><td colspan="5" class="download-empty">{{ loading ? '正在加载保存记录…' : appliedFilters.rules.length ? '没有符合条件的保存记录。' : '暂无保存记录。请在上方目录文件中点击“保存到系统”。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="download-pagination"><span>共 {{ total }} 条 · 第 {{ page }} / {{ pages }} 页</span><button :disabled="page <= 1 || loading || filtersDirty" @click="refresh(page - 1)">上一页</button><button :disabled="page >= pages || loading || filtersDirty" @click="refresh(page + 1)">下一页</button></div>
  </section>
</template>

<style scoped>
.download-panel { margin-top:22px; min-width:0; }
.download-search { margin:0 22px 18px; }
.download-search summary { cursor:pointer; color:var(--blue); font-size:12px; padding:10px 0; }
.download-search-actions { display:flex; gap:12px; align-items:center; justify-content:space-between; margin-top:12px; }
.download-search-actions small { font-size:11px; color:var(--muted); }
button { min-height:34px; padding:7px 12px; border:1px solid #c3d3dc; background:white; color:var(--blue); cursor:pointer; font-size:11px; }
button.primary { background:var(--blue); color:white; border-color:var(--blue); }
button:disabled { opacity:.55; cursor:not-allowed; }
.download-hint,.download-error { margin:0 22px 14px; padding:11px 13px; font-size:12px; line-height:1.65; background:#eef7fa; color:#26596d; overflow-wrap:anywhere; }
.download-error { background:#fff0ee; color:#913a31; }
.download-table-wrap { overflow-x:auto; }
.download-table { border-collapse:collapse; width:100%; min-width:900px; font-size:11px; }
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
.download-pagination { display:flex; justify-content:flex-end; gap:12px; align-items:center; padding:15px 22px; font-size:11px; color:var(--muted); }
</style>
