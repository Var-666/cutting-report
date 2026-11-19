<script setup lang="ts">
import { computed, reactive, shallowRef, watch, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import { NAlert, NButton, NDatePicker, NInput, NSelect, NTag } from 'naive-ui'
import AppReportTable from '../components/AppReportTable.vue'
import AppIcon from '../components/AppIcon.vue'
import ReportFilterDialog from '../components/ReportFilterDialog.vue'
import OperationsDrawer from '../components/operations/OperationsDrawer.vue'
import OperationsPageHeader from '../components/operations/OperationsPageHeader.vue'
import { incidentStatus, roles, stamp, tone } from '../components/operations/presentation'
import { operations, type IncidentQuery, type LogQuery } from '../api/operations'
import type { Incident, LogPage, LogRecord, PageIncident } from '../api/generated/types.gen'
import { deviceSelection } from '../state/devices'

const { mode } = defineProps<{ mode: 'incidents' | 'logs' }>()
const route = useRoute()
const isLogs = computed(() => mode === 'logs')
const incidentPage = shallowRef<PageIncident | null>(null)
const logPage = shallowRef<LogPage | null>(null)
const cursors = shallowRef<(string | null)[]>([null])
const logIndex = shallowRef(0)
const incidentId = shallowRef<string | null>(null)
const selectedLog = shallowRef<LogRecord | null>(null)
const error = shallowRef('')
const loading = shallowRef(false)
let controller: AbortController | null = null
const exportRequest = shallowRef<AbortController | null>(null)
const exportFeedback = shallowRef<{ type: 'info' | 'warning' | 'error'; text: string } | null>(null)
const draft = reactive<LogQuery & IncidentQuery>({})
const range = shallowRef<[number, number] | null>(null)
const applied = shallowRef<LogQuery & IncidentQuery>({})
const appliedRange = shallowRef<[number, number] | null>(null)
const filtersOpen = shallowRef(false)
const filterError = shallowRef('')
const title = computed(() => isLogs.value ? '运行日志' : '异常事件')
const levels = (['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const).map(value => ({ value, label: value }))
const options = (labels: Record<string, string>) => Object.entries(labels).map(([value, label]) => ({ value, label }))
const lines = computed(() => deviceSelection.items.map(line => ({ value: line.id, label: line.name })))
type FilterField = 'component' | 'error_code' | 'keyword' | 'instance_id' | 'request_id' | 'analysis_id' | 'task_id' | 'report_id' | 'resource_id' | 'source_id'
const fields: { key: FilterField; label: string; logs?: boolean }[] = [
  { key: 'component', label: '业务模块' }, { key: 'error_code', label: '错误代码' },
  { key: 'keyword', label: '关键字（至少 2 字）' },
  { key: 'instance_id', label: '实例 ID', logs: true },
  { key: 'request_id', label: '请求 ID', logs: true }, { key: 'analysis_id', label: '分析 ID', logs: true },
  { key: 'task_id', label: '后台任务 ID', logs: true }, { key: 'report_id', label: '报表 ID', logs: true },
  { key: 'resource_id', label: '关联资源 ID', logs: true }, { key: 'source_id', label: '数据源 ID', logs: true },
]
const timeLabel = computed(() => {
  if (appliedRange.value) return `${stamp(new Date(appliedRange.value[0]).toISOString())} 至 ${stamp(new Date(appliedRange.value[1]).toISOString())}`
  if (isLogs.value) return '最近 24 小时'
  return applied.value.status === 'open' || applied.value.status === 'acknowledged' ? '不限起始时间' : '最近 30 天'
})
const filterTags = computed(() => {
  const { level, role, status, line_id: line } = applied.value
  return [
    { key: 'level', label: '级别', value: level },
    { key: 'role', label: '运行角色', value: role && roles[role] },
    { key: 'status', label: '状态', value: status && incidentStatus[status] },
    { key: 'line_id', label: '产线', value: line && (lines.value.find(item => item.value === line)?.label || line) },
    ...fields.map(field => ({ key: field.key, label: field.key === 'keyword' ? '关键字' : field.label, value: applied.value[field.key] })),
  ].filter(tag => tag.value)
})
const incidentColumns = [
  { key: 'c0', title: '最近发生' }, { key: 'c1', title: '级别' }, { key: 'c2', title: '来源' },
  { key: 'c3', title: '异常事件' }, { key: 'c4', title: '次数', align: 'right' },
  { key: 'c5', title: '状态' }, { key: 'c6', title: '关联记录' }, { key: 'c7', title: '操作' },
] as const
const logColumns = [
  { key: 'c0', title: '时间' }, { key: 'c1', title: '级别' }, { key: 'c2', title: '角色 / 实例' },
  { key: 'c3', title: '模块' }, { key: 'c4', title: '消息' }, { key: 'c5', title: '关联标识' }, { key: 'c6', title: '操作' },
] as const
const coverage = {
  retention: '起始时间超出配置保留期',
  scan_limit: '已达扫描上限，请缩短时间范围或选择角色',
  rotation: '读取时发生日志轮转，可刷新重查',
  segment: '部分日志段或记录无法读取',
  coverage: '当前日志段无法确认覆盖整个起始时段',
  unavailable: '共享日志目录暂不可用',
}
const exportCoverage = { ...coverage, record_limit: '已达单次导出条数上限，请缩短时间范围或增加筛选条件' }

function snapshotRange() {
  const end = Date.now()
  return [end - (isLogs.value ? 1 : 30) * 86400_000, end] as [number, number]
}
function resetDraft() {
  for (const field of fields) draft[field.key] = undefined
  Object.assign(draft, { level: undefined, role: undefined, line_id: undefined, status: undefined })
  range.value = null
  filterError.value = ''
}
function openFilters() {
  resetDraft()
  Object.assign(draft, applied.value)
  range.value = appliedRange.value
  filtersOpen.value = true
}
function apply(value: [number, number] | null = range.value) {
  if (draft.keyword?.trim().length === 1) { filterError.value = '关键字至少需要 2 个字符。'; return false }
  const [start, end] = value || snapshotRange()
  if (end <= start || end - start > 90 * 86400_000) { filterError.value = '请选择最长 90 天的有效时间范围。'; return false }
  exportRequest.value?.abort()
  exportFeedback.value = null
  const base = {
    component: draft.component?.trim(), level: draft.level, line_id: draft.line_id,
    error_code: draft.error_code?.trim(), keyword: draft.keyword?.trim(),
    start: value || isLogs.value ? new Date(start).toISOString() : undefined, end: new Date(end).toISOString(),
  }
  applied.value = isLogs.value
    ? { ...base, role: draft.role, instance_id: draft.instance_id?.trim(), request_id: draft.request_id?.trim(),
      analysis_id: draft.analysis_id?.trim(), task_id: draft.task_id?.trim(), report_id: draft.report_id?.trim(),
      resource_id: draft.resource_id?.trim(), source_id: draft.source_id?.trim() }
    : { ...base, status: draft.status }
  appliedRange.value = value
  filtersOpen.value = false
  incidentPage.value = null
  logPage.value = null
  logIndex.value = 0
  cursors.value = [null]
  void load(1)
  return true
}

async function load(target: number) {
  controller?.abort()
  const active = new AbortController()
  controller = active
  const modeAtStart = mode
  loading.value = true
  error.value = ''
  try {
    if (modeAtStart === 'logs') {
      const page = await operations.logs({ ...applied.value, limit: 10, cursor: cursors.value[target - 1] }, active.signal)
      if (active.signal.aborted) return
      logPage.value = page
      logIndex.value = target - 1
      cursors.value = [...cursors.value.slice(0, target), ...(page.next_cursor ? [page.next_cursor] : [])]
    } else {
      const page = await operations.incidents({ ...applied.value, page: target, page_size: 10 }, active.signal)
      if (!active.signal.aborted) incidentPage.value = page
    }
  } catch (caught) {
    if (!active.signal.aborted) error.value = String(caught)
  } finally { if (controller === active) loading.value = false }
}

function refresh() {
  if (applied.value.end && !appliedRange.value) {
    const [start, end] = snapshotRange()
    applied.value = { ...applied.value, start: isLogs.value ? new Date(start).toISOString() : undefined,
      end: new Date(end).toISOString() }
    cursors.value = [null]
    void load(1)
  } else void load(isLogs.value ? logIndex.value + 1 : incidentPage.value?.page ?? 1)
}

async function exportLogs() {
  if (exportRequest.value) return
  const active = new AbortController()
  exportRequest.value = active
  exportFeedback.value = null
  try {
    const { blob, headers } = await operations.exportLogs(applied.value, active.signal)
    if (active.signal.aborted) return
    if (blob.type.split(';')[0] !== 'application/x-ndjson') throw new Error('服务器未返回 JSONL 日志文件')
    if (blob.size) {
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = headers.get('Content-Disposition')?.match(/filename="([^"]+)"/)?.[1] || 'operations-logs.jsonl'
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    }
    const flags = headers.get('X-Log-Coverage-Warnings')?.split(',') ?? []
    const warnings = Object.entries(exportCoverage).filter(([key]) => flags.includes(key)).map(([, message]) => message)
    const summary = blob.size ? '日志已导出' : '当前筛选范围内没有可导出的日志'
    const text = [summary, ...warnings].join('。') + '。'
    exportFeedback.value = { type: warnings.length ? 'warning' : 'info', text }
  } catch (caught) {
    if (!active.signal.aborted) exportFeedback.value = { type: 'error', text: `日志导出失败。${String(caught)}` }
  } finally {
    exportRequest.value = null
  }
}

function close() {
  incidentId.value = null
  selectedLog.value = null
}
function changed() { void load(incidentPage.value?.page ?? 1) }

watch(() => [mode, route.fullPath], () => {
  close()
  filtersOpen.value = false
  resetDraft()
  logPage.value = null
  incidentPage.value = null
  for (const field of fields) {
    const value = route.query[field.key]
    draft[field.key] = typeof value === 'string' ? value : undefined
  }
  draft.level = levels.find(option => option.value === route.query.level)?.value
  const role = route.query.role
  draft.role = role === 'web' || role === 'scheduler' || role === 'worker' || role === 'automation' ? role : undefined
  draft.line_id = typeof route.query.line_id === 'string' ? route.query.line_id : undefined
  draft.status = route.query.status === 'open' || route.query.status === 'acknowledged' || route.query.status === 'recovered'
    ? route.query.status : undefined
  const start = typeof route.query.start === 'string' ? Date.parse(route.query.start) : NaN
  const end = typeof route.query.end === 'string' ? Date.parse(route.query.end) : NaN
  range.value = Number.isFinite(start) && Number.isFinite(end) ? [start, end] : null
  incidentId.value = !isLogs.value && typeof route.query.incident === 'string' ? route.query.incident : null
  if (!apply()) error.value = filterError.value
}, { immediate: true })

onBeforeUnmount(() => {
  controller?.abort()
  exportRequest.value?.abort()
})
</script>

<template>
  <div class="management-page records" :aria-busy="loading">
    <OperationsPageHeader :title="title" :icon="isLogs ? 'file' : 'bell'">
      <template #actions>
        <NButton type="primary" aria-haspopup="dialog" @click="openFilters">
          <template #icon><AppIcon name="search" :size="16" tone="current" /></template>筛选
        </NButton>
        <NButton @click="refresh" :loading="loading">刷新</NButton>
        <NButton v-if="isLogs" :loading="!!exportRequest" :disabled="loading || !logPage || !!error"
          title="导出已应用筛选条件下的日志" @click="exportLogs">
          <template #icon><AppIcon name="download" :size="16" tone="current" /></template>导出日志
        </NButton>
      </template>
      <div class="applied-filters" aria-label="已应用的筛选条件">
        <span>{{ timeLabel }}</span>
        <NTag v-for="tag in filterTags" :key="tag.key" size="small" :bordered="false" :title="`${tag.label}：${tag.value}`">
          <span class="filter-tag">{{ tag.label }}：{{ tag.value }}</span>
        </NTag>
      </div>
    </OperationsPageHeader>
    <ReportFilterDialog v-model:show="filtersOpen" :title="`${title}筛选`">
      <form class="filters" @submit.prevent="apply()">
        <label class="date"><span>时间范围</span><NDatePicker v-model:value="range" type="datetimerange" clearable start-placeholder="开始时间" end-placeholder="结束时间" />
          <small>{{ isLogs ? '留空使用最近 24 小时' : '留空使用最近 30 天，待处理和已确认不限起始时间' }}</small>
        </label>
        <label><span>级别</span><NSelect v-model:value="draft.level" :options="levels" clearable /></label>
        <label v-if="isLogs"><span>运行角色</span><NSelect v-model:value="draft.role" :options="options(roles)" clearable /></label>
        <label v-else><span>状态</span><NSelect v-model:value="draft.status" :options="options(incidentStatus)" clearable /></label>
        <label><span>产线</span><NSelect v-model:value="draft.line_id" :options="lines" filterable clearable /></label>
        <template v-for="field in fields" :key="field.key">
          <label v-if="isLogs || !field.logs"><span>{{ field.label }}</span><NInput v-model:value="draft[field.key]" clearable :maxlength="field.key === 'keyword' ? 100 : 128" /></label>
        </template>
        <NAlert v-if="filterError" class="filter-error" type="error" role="alert">{{ filterError }}</NAlert>
        <div class="actions filter-actions">
          <NButton attr-type="button" @click="resetDraft">重置</NButton>
          <NButton attr-type="button" @click="filtersOpen = false">取消</NButton>
          <NButton type="primary" attr-type="submit">应用筛选</NButton>
        </div>
      </form>
    </ReportFilterDialog>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <NAlert v-if="isLogs && exportFeedback" :type="exportFeedback.type" role="status">{{ exportFeedback.text }}</NAlert>
    <NAlert v-if="isLogs && logPage?.coverage_warnings.length" type="warning" title="查询范围受限">
      {{ logPage.coverage_warnings.map(key => coverage[key]).join('。') }}。
      <span v-if="logPage.earliest_scanned_at">已扫描 {{ stamp(logPage.earliest_scanned_at) }} 至 {{ stamp(logPage.latest_scanned_at) }}。</span>
    </NAlert>
    <template v-if="isLogs">
      <AppReportTable :items="logPage?.items" :columns="logColumns" :row-key="row => row.id" :loading="loading" :paginate="false">
        <template #c0="{ row }">{{ stamp(row.timestamp) }}</template>
        <template #c1="{ row }"><NTag size="small" :type="tone(row.level)">{{ row.level }}</NTag></template>
        <template #c2="{ row }">{{ roles[row.role] }} / {{ row.instance_id }}</template>
        <template #c3="{ row }">{{ row.component }}</template>
        <template #c4="{ row }">{{ row.message }}</template>
        <template #c5="{ row }">{{ [...new Set([row.request_id, row.analysis_id, row.task_id, row.report_id, row.resource_id].filter(Boolean))].join(' / ') || '—' }}</template>
        <template #c6="{ row }"><NButton size="tiny" @click="selectedLog = row">详情</NButton></template>
      </AppReportTable>
      <div v-if="logPage" class="actions pages">
        <NButton size="small" :disabled="loading || logIndex === 0" @click="load(logIndex)">上一页</NButton>
        <span>第 {{ logIndex + 1 }} 页</span>
        <NButton size="small" :disabled="loading || !logPage.next_cursor" @click="load(logIndex + 2)">下一页</NButton>
      </div>
    </template>
    <AppReportTable v-else :items="incidentPage?.items" :columns="incidentColumns" :row-key="row => row.incident_id"
      :loading="loading" :page="incidentPage?.page ?? 1" :page-size="10"
      :total="Math.min(3000, incidentPage?.total ?? 0)" @page="load">
      <template #c0="{ row }">{{ stamp(row.last_seen_at) }}</template>
      <template #c1="{ row }"><NTag size="small" :type="tone(row.level)">{{ row.level }}</NTag></template>
      <template #c2="{ row }">{{ roles[row.role] }} / {{ row.component }}</template>
      <template #c3="{ row }">{{ row.message }}</template>
      <template #c4="{ row }">{{ row.occurrences }}</template>
      <template #c5="{ row }"><NTag size="small" :type="row.status === 'open' ? 'warning' : row.status === 'recovered' ? 'success' : 'info'">{{ incidentStatus[row.status] }}</NTag></template>
      <template #c6="{ row }">{{ row.context.report_id || row.context.task_id || row.resource_id || '—' }}</template>
      <template #c7="{ row }"><NButton size="tiny" @click="incidentId = row.incident_id">详情</NButton></template>
    </AppReportTable>
    <NAlert v-if="!isLogs && (incidentPage?.total ?? 0) > 3000" type="warning">事件超过 3000 条，请缩短范围或增加筛选条件。</NAlert>
    <OperationsDrawer :incident-id="incidentId" :log="selectedLog" @close="close" @changed="changed" />
  </div>
</template>

<style scoped>
.records { display:grid; gap:var(--space-4); min-width:0; }
.applied-filters, .actions { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); }
.applied-filters { margin-top:var(--space-3); color:var(--text-color-3); font-size:var(--font-auxiliary); }
.applied-filters > span { overflow-wrap:anywhere; }
.filter-tag { display:block; max-width:min(240px, 70vw); overflow:hidden; text-overflow:ellipsis; }
.filters { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:var(--space-3); }
.filters label { min-width:0; display:grid; gap:var(--space-1); color:var(--text-color-2); font-size:var(--font-control); }
.filters small { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.filters .date, .filter-error, .filter-actions { grid-column:1 / -1; }
.filter-actions, .pages { justify-content:flex-end; }
@media (max-width:570px) { .filters { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:380px) { .filters { grid-template-columns:minmax(0,1fr); } }
</style>
