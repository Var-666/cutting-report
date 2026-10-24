<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NCard, NInput, NTag } from 'naive-ui'
import { getConfigurationDiagnostics } from '../api/reports.js'
import AppSelect from '../components/AppSelect.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

const report = ref(null)
const loading = ref(false)
const error = ref('')
const category = ref('')
const search = ref('')
const status = ref('')
const statuses = { effective: '已生效', pending: '待重启', error: '配置异常', unconfigured: '未配置' }
const tones = { effective: 'success', pending: 'warning', error: 'error', unconfigured: 'default' }
const rows = computed(() => (report.value?.items || []).filter(item =>
  (!category.value || item.category === category.value)
  && (!status.value || item.status === status.value)
  && item.key.toLowerCase().includes(search.value.trim().toLowerCase()),
))
let controller
let sequence = 0

function display(value) {
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
  } catch (exception) {
    if (current === sequence && exception.name !== 'AbortError') error.value = '配置诊断读取失败，请稍后重试。'
  } finally {
    if (current === sequence) loading.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => { sequence++; controller?.abort() })
</script>

<template>
  <div class="management-page configuration-diagnostics-page" :aria-busy="loading">
    <ManagementPageHero eyebrow="CONFIGURATION DIAGNOSTICS" title="配置诊断" description="核对实际运行快照、保存目标与配置来源。认证信息仅显示配置状态。" icon="settings" status-label="访问方式" status-value="管理员只读" tone="teal" />
    <NAlert v-if="error" type="error" role="alert">{{ error }}<span v-if="report">以下保留上次结果。</span><NButton :disabled="loading" @click="load">重试</NButton></NAlert>
    <p v-if="loading" role="status">正在读取配置快照…</p>
    <template v-if="report">
      <NAlert type="info">{{ report.precedence }}<p v-for="note in report.notes" :key="note">{{ note }}</p></NAlert>
      <NAlert v-for="message in report.errors" :key="message" type="error" role="alert">{{ message }}</NAlert>
      <NAlert v-if="!report.processes.worker.available" type="warning" role="status">Worker 快照不可用（{{ report.processes.worker.state }}），请检查独立 Worker 进程与心跳。</NAlert>
      <NCard :bordered="false" title="进程加载状态">
        <div class="processes">
          <div v-for="role in ['web', 'worker']" :key="role"><strong>{{ role === 'web' ? 'Web' : 'Worker' }}</strong><p>配置版本：{{ report.processes[role].version || '未知' }}</p><p>启动加载：{{ report.processes[role].loaded_at || '未知' }}</p><p>热更新：{{ report.processes[role].hot_loaded_at || '未知' }} · 修订 {{ report.processes[role].hot_revision ?? '未知' }}</p></div>
        </div>
      </NCard>
      <NCard :bordered="false" title="配置项">
        <div class="diagnostics-filters">
          <label><span>配置分类</span><AppSelect v-model="category" aria-label="配置分类"><option value="">全部分类</option><option v-for="item in report.categories" :key="item" :value="item">{{ item }}</option></AppSelect></label>
          <label><span>配置项名称</span><NInput v-model:value="search" placeholder="搜索配置项名称" :input-props="{ 'aria-label': '配置项名称' }" clearable /></label>
          <label><span>生效状态</span><AppSelect v-model="status" aria-label="生效状态"><option value="">全部状态</option><option v-for="(label, key) in statuses" :key="key" :value="key">{{ label }}</option></AppSelect></label>
          <NButton :loading="loading" :disabled="loading" @click="load">刷新快照</NButton>
        </div>
        <div class="history-wrap diagnostics-table"><table v-content-table class="history-table"><caption class="sr-only">Web、Worker 配置来源和生效状态</caption><thead><tr><th>配置项</th><th>Web 当前生效值 / 来源</th><th>Worker 当前生效值 / 来源</th><th>已保存目标 / 来源</th><th>生效状态及规则</th></tr></thead><tbody>
          <tr v-for="row in rows" :key="row.key"><td><strong>{{ row.key }}</strong><small>{{ row.category }}</small></td><td v-for="role in ['web', 'worker']" :key="role">{{ display(row.processes[role].value) }}<small>{{ row.processes[role].source }}</small><NTag size="small" :type="tones[row.processes[role].status]">{{ statuses[row.processes[role].status] }}</NTag></td><td>{{ display(row.saved_value) }}<small>{{ row.saved_source }}</small></td><td><NTag :type="tones[row.status]">{{ statuses[row.status] }}</NTag><small v-if="row.process_mismatch">Web / Worker 配置版本不一致</small><small v-if="!row.valid">目标配置无效，请核对对应配置项及文件</small><small>{{ row.rule }}</small></td></tr>
          <tr v-if="!rows.length"><td colspan="5" class="diagnostics-empty" role="status">没有符合条件的配置项。</td></tr>
        </tbody></table></div>
      </NCard>
    </template>
    <NButton v-else-if="!loading && !error" @click="load">读取配置诊断</NButton>
  </div>
</template>

<style scoped>
.configuration-diagnostics-page{display:grid;gap:16px;min-width:0}.diagnostics-filters{display:grid;grid-template-columns:1fr 2fr 1fr auto;gap:12px;align-items:end;margin-bottom:16px}.diagnostics-filters label{min-width:0}.diagnostics-filters label>span{display:block;margin-bottom:6px;color:var(--text-color-3);font-size:13px}.processes{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.processes p{overflow-wrap:anywhere;color:var(--text-color-3);font-size:12px}.diagnostics-table{overflow:auto}.diagnostics-table table{min-width:1050px}.diagnostics-table td{vertical-align:top;overflow-wrap:anywhere;max-width:300px}.diagnostics-table small{display:block;margin:6px 0;color:var(--text-color-3);line-height:1.6}.diagnostics-empty{text-align:center;padding:24px!important}
@media(max-width:900px){.diagnostics-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.diagnostics-filters,.processes{grid-template-columns:1fr}}
</style>
