<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from 'vue'
import { NAlert, NButton, NForm, NFormItem, NInput, NInputNumber, NModal } from 'naive-ui'
import DatabaseMonitorTable from './DatabaseMonitorTable.vue'
import AppReportTable from './AppReportTable.vue'
import type { DatabaseMonitor, DatabaseMonitorInput, DatabaseMonitorIncident } from '../api/generated/types.gen'
import { listDatabaseMonitors, saveDatabaseMonitor, deleteDatabaseMonitor, listDatabaseMonitorIncidents } from '../api/databaseMonitors'
import { toError } from '../utils/errors'
import { formatChineseDateTime } from '../utils/dateDisplay'
import { useConfirm } from '../composables/useConfirm'
import { useUnsavedChanges } from '../composables/useUnsavedChanges'

const items = ref<DatabaseMonitor[]>([])
const incidents = ref<DatabaseMonitorIncident[]>([])
const page = ref(1)
const total = ref(0)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const editorError = ref('')
const editingId = ref<string>()
const showEditor = ref(false)
const dirty = ref(false)
const draft = ref({ name: '', ip: '', port: null as number | null })
const now = ref(Date.now() / 1000)
const timezone = ref('Asia/Shanghai')
const confirm = useConfirm()
useUnsavedChanges(dirty, '监控目标尚未保存，确定离开吗？')
let controller: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined
let active = false
const summary = computed(() => `共 ${items.value.length} 个监控目标，${items.value.filter(item => !item.enabled).length} 个已暂停`)
const historyColumns = [
  { key: 'c0', title: '监控名称', align: 'left' }, { key: 'c1', title: 'IP / 端口', align: 'left' },
  { key: 'c2', title: '故障开始', align: 'left' }, { key: 'c3', title: '结束时间', align: 'left' },
  { key: 'c4', title: '持续时长', align: 'right' }, { key: 'c5', title: '状态', align: 'center' },
  { key: 'c6', title: '失败原因', align: 'left' },
] as const
const endLabels = { recovered: '已恢复', paused: '已暂停监控', changed: '目标已变更', deleted: '已删除监控' }
function stamp(value: number | null) { return value === null ? '—' : formatChineseDateTime(new Date(value * 1000).toLocaleString('sv-SE', { timeZone: timezone.value })) }
function duration(item: DatabaseMonitorIncident) {
  const minutes = Math.max(0, Math.floor(((item.ended_at ?? now.value) - item.started_at) / 60))
  return minutes < 60 ? `${minutes} 分钟` : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`
}
function schedule() {
  clearTimeout(timer)
  if (active) timer = setTimeout(async () => { now.value = Date.now() / 1000; await load(); schedule() }, 15_000)
}
async function load(targetPage = page.value) {
  if (!active || controller) return
  const requestController = new AbortController()
  controller = requestController
  loading.value = true
  try {
    const [targets, history] = await Promise.all([listDatabaseMonitors({ signal: controller.signal }), listDatabaseMonitorIncidents(targetPage, { signal: controller.signal })])
    if (!active || controller !== requestController || requestController.signal.aborted) return
    items.value = targets.items
    timezone.value = targets.timezone
    incidents.value = history.items
    total.value = history.total
    page.value = history.page
    now.value = Date.now() / 1000
    error.value = ''
  } catch (caught) {
    if (active && !requestController.signal.aborted) error.value = toError(caught).message
  } finally {
    if (controller === requestController) { controller = null; loading.value = false }
  }
}
function open(item?: DatabaseMonitor) {
  editingId.value = item?.monitor_id
  draft.value = { name: item?.name ?? '', ip: item?.ip ?? '', port: item?.port ?? null }
  editorError.value = ''
  dirty.value = false
  showEditor.value = true
}
async function close() {
  if (busy.value || dirty.value && !await confirm('修改尚未保存，确定关闭吗？')) return
  showEditor.value = false
  dirty.value = false
}
async function save() {
  if (busy.value) return
  if (!draft.value.name.trim() || !draft.value.ip.trim() || draft.value.port === null) {
    editorError.value = '请填写名称、IP 地址和端口'
    return
  }
  busy.value = true
  editorError.value = ''
  try {
    const existing = items.value.find(item => item.monitor_id === editingId.value)
    await saveDatabaseMonitor({ ...draft.value, port: draft.value.port, enabled: existing?.enabled ?? true }, editingId.value)
    dirty.value = false
    showEditor.value = false
    controller?.abort()
    controller = null
    await load()
  } catch (caught) { editorError.value = toError(caught).message }
  finally { busy.value = false }
}
async function toggle(item: DatabaseMonitor) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const input: DatabaseMonitorInput = { name: item.name, ip: item.ip, port: item.port, enabled: !item.enabled }
    await saveDatabaseMonitor(input, item.monitor_id)
    controller?.abort(); controller = null
    await load()
  } catch (caught) { error.value = toError(caught).message }
  finally { busy.value = false }
}
async function remove(item: DatabaseMonitor) {
  if (busy.value || !await confirm(`删除“${item.name}”的监控？故障历史会保留。`)) return
  busy.value = true
  try {
    await deleteDatabaseMonitor(item.monitor_id)
    controller?.abort(); controller = null
    await load()
  } catch (caught) { error.value = toError(caught).message }
  finally { busy.value = false }
}
onMounted(async () => { active = true; await load(); schedule() })
onBeforeUnmount(() => { active = false; clearTimeout(timer); controller?.abort() })
</script>

<template>
  <section class="database-monitor-panel">
    <header class="monitor-heading">
      <div><h1>数据库监控</h1><p>{{ summary }}</p></div>
      <div class="monitor-toolbar"><NButton :loading="loading" :disabled="busy" @click="load()">刷新</NButton><NButton type="primary" :disabled="busy" @click="open()">添加监控</NButton></div>
    </header>
    <p class="monitor-note">每分钟检测 IP 和端口；连续失败 3 次告警，连续成功 2 次恢复。端口可达不代表数据库查询正常。</p>
    <NAlert v-if="error" type="error" role="alert" title="监控数据读取失败">{{ error }}；当前连接状态无法确认。<NButton size="small" :loading="loading" @click="load()">重试</NButton></NAlert>
    <DatabaseMonitorTable :items="items" :loading="loading" :busy="busy" :now="now" :timezone="timezone" :unavailable="Boolean(error)" @edit="open" @toggle="toggle" @delete="remove" />
    <h2>故障记录</h2>
    <AppReportTable :columns="historyColumns" :items="incidents" :loading="loading" :total="total" :page="page" :page-size="15" :page-sizes="[]" :row-key="item => item.incident_id" aria-label="数据库故障记录" @page="load">
      <template #c0="{ row }">{{ row.name }}</template>
      <template #c1="{ row }">{{ row.ip.includes(':') ? `[${row.ip}]:${row.port}` : `${row.ip}:${row.port}` }}</template>
      <template #c2="{ row }">{{ stamp(row.started_at) }}</template>
      <template #c3="{ row }">{{ stamp(row.ended_at) }}</template>
      <template #c4="{ row }">{{ duration(row) }}</template>
      <template #c5="{ row }">{{ row.end_reason ? endLabels[row.end_reason] : '未恢复' }}</template>
      <template #c6="{ row }">{{ row.last_error }}</template>
      <template #empty>暂无故障记录</template>
    </AppReportTable>
    <NModal :show="showEditor" preset="card" :title="editingId ? '编辑监控' : '添加监控'" style="width:min(480px, calc(100vw - 32px))" :mask-closable="false" :close-on-esc="!busy" @close="close" @esc="close">
      <NForm label-placement="top" @submit.prevent="save">
        <NFormItem label="名称" label-for="monitor-name" required><NInput v-model:value="draft.name" :input-props="{ id: 'monitor-name' }" :maxlength="80" :disabled="busy" placeholder="例如：切割生产数据库" @update:value="dirty = true" /></NFormItem>
        <NFormItem label="IP 地址" label-for="monitor-ip" required><NInput v-model:value="draft.ip" :input-props="{ id: 'monitor-ip' }" :disabled="busy" placeholder="例如：192.168.1.10" @update:value="dirty = true" /></NFormItem>
        <NFormItem label="数据库端口" label-for="monitor-port" required><NInputNumber v-model:value="draft.port" style="width:100%" :input-props="{ id: 'monitor-port' }" :min="1" :max="65535" :precision="0" :show-button="false" :disabled="busy" placeholder="填写实际端口" @update:value="dirty = true" /></NFormItem>
        <NAlert v-if="editorError" type="error" role="alert">{{ editorError }}</NAlert>
        <footer class="monitor-toolbar"><NButton :disabled="busy" @click="close">取消</NButton><NButton type="primary" attr-type="submit" :loading="busy">保存</NButton></footer>
      </NForm>
    </NModal>
  </section>
</template>

<style scoped>
.database-monitor-panel { display:grid; gap:16px; padding:24px; min-width:0; border:1px solid var(--theme-border-color); border-radius:var(--report-panel-radius); background:rgb(var(--container-bg-color)); }
.monitor-heading { display:flex; justify-content:space-between; align-items:center; gap:16px; }
.monitor-heading h1, h2 { margin:0; font-size:var(--font-section-title); color:var(--text-color-1); }
.monitor-heading p, .monitor-note { margin:4px 0 0; color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.6; }
.monitor-toolbar { display:flex; gap:8px; justify-content:flex-end; }
@media(max-width:620px) { .database-monitor-panel { padding:16px; } .monitor-heading { align-items:flex-start; flex-wrap:wrap; } }
</style>
