<script setup lang="ts">
import type { TagProps } from 'naive-ui'
import type { ConfigurationDiagnostics, DiagnosticStatus } from '../types/configuration'

import { toError } from '../utils/errors'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { NAlert, NButton, NCard, NInput, NTag } from 'naive-ui'
import { getConfigurationDiagnostics } from '../api/configuration.ts'
import AppSelect from '../components/AppSelect.vue'
import AppReportTable from '../components/AppReportTable.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

const report = ref<ConfigurationDiagnostics | null>(null)
const loading = ref(false)
const error = ref('')
const category = ref('')
const search = ref('')
const page = ref(1)
const pageSize = ref(10)
const statuses = { effective: '已生效', pending: '待重启', error: '配置异常', unconfigured: '未配置' }
const tones: Record<DiagnosticStatus, TagProps['type']> = { effective: 'success', pending: 'warning', error: 'error', unconfigured: 'default' }
const rows = computed(() => (report.value?.items || []).filter(item =>
  item.status === 'error'
  && (!category.value || item.category === category.value)
  && item.key.toLowerCase().includes(search.value.trim().toLowerCase()),
))
const visibleRows = computed(() => rows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
watch([category, search, pageSize, report], () => { page.value = 1 })
let controller: AbortController | undefined
let sequence = 0

function display(value: unknown) {
  if (value === null || value === undefined) return '未配置'
  if (typeof value === 'boolean') return value ? '启用' : '关闭'
  return Array.isArray(value) ? value.join('、') : String(value)
}

async function load() {
  const current = ++sequence
  controller?.abort()
  controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const data = await getConfigurationDiagnostics({ signal: controller.signal })
    if (current === sequence) report.value = data
  } catch (caught) {
    const exception = toError(caught)
    if (current === sequence && exception.name !== 'AbortError') error.value = '配置诊断读取失败，请稍后重试。'
  } finally {
    if (current === sequence) loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { sequence++; controller?.abort() })
const diagnosticsTableColumns = [
  { key: 'c0', title: '配置项', width: 260, align: 'left', fixed: 'left', ellipsis: false },
  { key: 'c1', title: 'Web 当前生效值 / 来源', minWidth: 320, align: 'left', ellipsis: false },
  { key: 'c2', title: 'Worker 当前生效值 / 来源', minWidth: 320, align: 'left', ellipsis: false },
  { key: 'c3', title: '已保存目标 / 来源', minWidth: 280, align: 'left', ellipsis: false },
  { key: 'c4', title: '生效状态及规则', minWidth: 340, align: 'left', ellipsis: false },
] as const
</script>

<template>
  <div class="management-page configuration-diagnostics-page" :aria-busy="loading">
    <ManagementPageHero eyebrow="CONFIGURATION DIAGNOSTICS" title="配置诊断" description="核对实际运行快照、保存目标与配置来源。认证信息仅显示配置状态。" icon="settings" status-label="访问方式" status-value="管理员只读" tone="teal" />
    <NAlert v-if="error" type="error" role="alert">{{ error }}<span v-if="report">以下保留上次结果。</span><NButton :disabled="loading" @click="load">重试</NButton></NAlert>
    <p v-if="loading" role="status">正在读取配置快照…</p>
    <template v-if="report">
      <NAlert v-for="message in report.errors" :key="message" type="error" role="alert">{{ message }}</NAlert>
      <NAlert v-if="!report.processes.worker.available" type="warning" role="status">Worker 快照不可用（{{ report.processes.worker.state }}），请检查独立 Worker 进程与心跳。</NAlert>
      <NCard :bordered="false" title="进程加载状态">
        <div class="processes">
          <div v-for="role in (['web', 'worker'] as const)" :key="role"><strong>{{ role === 'web' ? 'Web' : 'Worker' }}</strong><p>配置版本：{{ report.processes[role].version || '未知' }}</p><p>启动加载：{{ report.processes[role].loaded_at || '未知' }}</p><p>热更新：{{ report.processes[role].hot_loaded_at || '未知' }} · 修订 {{ report.processes[role].hot_revision ?? '未知' }}</p></div>
        </div>
      </NCard>
      <NCard :bordered="false" title="异常配置项">
        <div class="diagnostics-filters">
          <label><span>配置分类</span><AppSelect v-model="category" aria-label="配置分类"><option value="">全部分类</option><option v-for="item in report.categories" :key="item" :value="item">{{ item }}</option></AppSelect></label>
          <label><span>配置项名称</span><NInput v-model:value="search" placeholder="搜索配置项名称" :input-props="{ 'aria-label': '配置项名称' }" clearable /></label>
          <NButton :loading="loading" :disabled="loading" @click="load">刷新快照</NButton>
        </div>
        <div class="diagnostics-table">
          <AppReportTable :columns="diagnosticsTableColumns" :items="visibleRows" :row-key="row => row.key" :total="rows.length" :page="page" :page-size="pageSize" :page-sizes="[10, 20, 50]" :loading="loading" aria-label="Web、Worker 配置来源和生效状态及异常配置项分页" @page="page = $event" @page-size="pageSize = $event">
            <template #c0="{ row }"><div class="diagnostic-cell"><strong :title="row.key">{{ row.key }}</strong><small :title="row.category">{{ row.category }}</small></div></template>
            <template v-for="(role, index) in (['web', 'worker'] as const)" :key="role" #[`c${index+1}`]="{ row }"><div class="diagnostic-cell"><NTag size="small" :type="tones[row.processes[role].status]">{{ statuses[row.processes[role].status] }}</NTag><span class="diagnostic-value" :title="display(row.processes[role].value)">{{ display(row.processes[role].value) }}</span><small class="diagnostic-source" :title="row.processes[role].source">来源：{{ row.processes[role].source }}</small></div></template>
            <template #c3="{ row }"><div class="diagnostic-cell"><span class="diagnostic-value" :title="display(row.saved_value)">{{ display(row.saved_value) }}</span><small class="diagnostic-source" :title="row.saved_source">来源：{{ row.saved_source }}</small></div></template>
            <template #c4="{ row }"><div class="diagnostic-cell"><NTag size="small" :type="tones[row.status]">{{ statuses[row.status] }}</NTag><small v-if="row.process_mismatch">Web / Worker 配置版本不一致</small><small v-if="!row.valid">目标配置无效，请核对对应配置项及文件</small><small :title="row.rule">{{ row.rule }}</small></div></template>
            <template #empty><span role="status">{{ category || search ? '没有符合条件的异常配置项。' : '暂无异常配置项。' }}</span></template>
          </AppReportTable>
        </div>
      </NCard>
    </template>
    <NButton v-else-if="!loading && !error" @click="load">读取配置诊断</NButton>
  </div>
</template>

<style scoped>
.configuration-diagnostics-page{display:grid;gap:16px;min-width:0}.diagnostics-filters{display:grid;grid-template-columns:1fr 2fr auto;gap:12px;align-items:end;margin-bottom:16px}.diagnostics-filters label{min-width:0}.diagnostics-filters label>span{display:block;margin-bottom:6px;color:var(--text-color-3);font-size:13px}.processes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.processes p{overflow-wrap:anywhere;color:var(--text-color-3);font-size:12px}.diagnostics-table{min-width:0}.diagnostic-cell{display:flex;align-items:center;white-space:nowrap;max-width:100%;gap:12px;min-width:0}.diagnostic-cell>strong,.diagnostic-cell>small{white-space:nowrap;min-width:0;overflow:hidden;text-overflow:ellipsis}.diagnostic-cell>small{color:var(--text-color-3);font-size:var(--font-auxiliary)}.diagnostic-source{border-left:1px solid var(--theme-border-color);padding-left:10px}.diagnostic-value{flex:0 1 auto;min-width:0;max-width:100%;overflow:hidden;text-overflow:ellipsis}.diagnostic-cell :deep(.n-tag){flex-shrink:0}
@media(max-width:900px){.diagnostics-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.diagnostics-filters,.processes{grid-template-columns:1fr}}
</style>
