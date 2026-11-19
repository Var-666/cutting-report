<script setup lang="ts">
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NCard, NEmpty, NSpin, NTag } from 'naive-ui'
import AppReportTable from '../components/AppReportTable.vue'
import HomeKpiCard from '../components/HomeKpiCard.vue'
import OperationsPageHeader from '../components/operations/OperationsPageHeader.vue'
import { health, incidentStatus, links, roles, stamp, tone, workGroup, workStatus } from '../components/operations/presentation'
import type { OperationsOverview, OperationsStatus } from '../api/generated/types.gen'
import { operations } from '../api/operations'

const { statusOnly = false } = defineProps<{ statusOnly?: boolean }>()
const overview = shallowRef<OperationsOverview | null>(null)
const status = shallowRef<OperationsStatus | null>(null)
const error = shallowRef('')
const loading = shallowRef(false)
const rolePage = shallowRef(1)
const rolePageSize = 10
const roleCount = computed(() => status.value?.roles.length ?? 0)
const roleItems = computed(() => status.value?.roles.slice((rolePage.value - 1) * rolePageSize, rolePage.value * rolePageSize) ?? [])
watch(roleCount, count => { rolePage.value = Math.min(rolePage.value, Math.max(1, Math.ceil(count / rolePageSize))) })
let active: AbortController | null = null
let timer: ReturnType<typeof setTimeout> | undefined
let mounted = true
const cards = computed(() => {
  const state = status.value
  const roleStates = state?.roles.filter(item => !item.instance_id) ?? []
  const monitors = state?.monitors
  const work = state?.work
  const recent = overview.value
  return [
    { title: '待处理异常', icon: 'bell', value: recent?.open_incidents ?? '未知', to: { name: 'operations-incidents', query: { status: 'open' } },
      help: '待管理员确认', color: 'rgb(var(--error-color))' },
    { title: '近 24 小时错误 / 警告', icon: 'file', value: recent?.log_totals ? `${recent.log_totals.errors} / ${recent.log_totals.warnings}` : '未知',
      help: recent?.log_totals?.complete ? '错误包含严重错误' : '当前可检索日志的计数', to: { name: 'operations-logs' }, color: 'rgb(var(--warning-color))' },
    { title: '运行角色', icon: 'activity', value: roleStates.every(item => item.state === 'unknown') ? '未知'
      : `${roleStates.filter(item => item.state === 'healthy').length} / ${roleStates.length}`,
      help: '正常角色 / 全部角色', to: { name: 'operations-status' }, color: 'rgb(var(--info-color))' },
    { title: 'TCP 监控异常', icon: 'database', value: monitors ? monitors.filter(item => item.state === 'down' || item.state === 'recovering').length : '未知',
      help: monitors ? `未知 ${monitors.filter(item => item.state === 'unknown').length} · 暂停 ${monitors.filter(item => item.state === 'paused').length}` : '等待监控状态',
      to: { name: 'database-monitors' }, color: 'rgb(var(--warning-color))' },
    { title: '工作异常', icon: 'clock', value: work ? work.filter(item => ['failed', 'blocked', 'unknown'].includes(item.status)).reduce((n, item) => n + item.count, 0) : '未知',
      help: '失败、阻塞和结果待核对记录', to: { name: 'operations-status' }, color: 'rgb(var(--error-color))' },
  ]
})
const roleColumns = [
  { key: 'c0', title: '运行角色' }, { key: 'c1', title: '实例' }, { key: 'c2', title: '状态' },
  { key: 'c3', title: '最近心跳' }, { key: 'c4', title: '心跳距今' }, { key: 'c5', title: '停滞执行器' },
  { key: 'c6', title: '配置加载' },
] as const
const monitorColumns = [
  { key: 'c0', title: '目标名称' }, { key: 'c1', title: 'TCP 状态' }, { key: 'c2', title: '上次检测' },
] as const
const incidentColumns = [
  { key: 'c0', title: '最近发生' }, { key: 'c1', title: '级别' }, { key: 'c2', title: '事件' },
  { key: 'c3', title: '次数', align: 'right' }, { key: 'c4', title: '状态' },
] as const
const workColumns = [
  { key: 'c0', title: '模块' }, { key: 'c1', title: '状态' }, { key: 'c2', title: '记录数', align: 'right' },
] as const
const roleDetails: Record<string, string> = {
  running: '运行中', lost: '租约已失效', stopped: '已停止', stalled: '执行停滞', missing: '缺少心跳',
  degraded: '就绪检查异常', unavailable: '暂不可用', capacity_shortfall: '正常实例不足',
}
const configurationLabels = { healthy: '加载正常', abnormal: '加载失败', unknown: '无有效快照' }
const shortcuts = [...links, { name: 'operations-incidents', label: '异常事件' }, { name: 'operations-logs', label: '运行日志' }]
function stateTone(state: string) {
  if (state === 'healthy') return 'success'
  if (['abnormal', 'down'].includes(state)) return 'error'
  if (['suspect', 'recovering', 'unknown'].includes(state)) return 'warning'
  return 'default'
}
async function refresh() {
  clearTimeout(timer)
  active?.abort()
  const controller = new AbortController()
  active = controller
  loading.value = true
  try {
    if (statusOnly) {
      const data = await operations.status(controller.signal)
      if (!controller.signal.aborted) status.value = data
    } else {
      const data = await operations.overview(controller.signal)
      if (!controller.signal.aborted) {
        overview.value = data
        status.value = data.runtime
      }
    }
    if (!controller.signal.aborted) error.value = ''
  } catch (caught) {
    if (!controller.signal.aborted) error.value = String(caught)
  } finally {
    if (active === controller && mounted) {
      loading.value = false
      timer = setTimeout(refresh, 30_000)
    }
  }
}
watch(() => statusOnly, () => { overview.value = null; status.value = null; void refresh() }, { immediate: true })
onBeforeUnmount(() => { mounted = false; active?.abort(); clearTimeout(timer) })
</script>

<template>
  <div class="management-page operations" :aria-busy="loading">
    <OperationsPageHeader :title="statusOnly ? '运行状态' : '运维概览'" icon="activity">
      <template #actions>
        <span v-if="status" class="secondary">更新于 {{ stamp(status.observed_at) }}</span>
        <NButton size="small" :loading="loading" @click="refresh">刷新</NButton>
      </template>
      <nav class="links" aria-label="运维管理入口">
        <RouterLink v-for="link in shortcuts" :key="link.name" :to="{ name: link.name }" custom v-slot="{ href, navigate }">
          <NButton tag="a" :href="href" size="small" @click="navigate">{{ link.label }}</NButton>
        </RouterLink>
      </nav>
    </OperationsPageHeader>
    <NAlert v-if="error" type="error" title="本次刷新失败">{{ error }}</NAlert>
    <NAlert v-else-if="status?.postgres === 'abnormal'" type="error" title="PostgreSQL 状态库不可用">角色和业务状态暂不可确认。</NAlert>
    <section v-if="!statusOnly" class="cards" aria-label="运维统计">
      <RouterLink v-for="card in cards" :key="card.title" :to="card.to" class="metric-link">
        <HomeKpiCard :title="card.title" :value="card.value" :icon="card.icon" :color="card.color" :trend-label="card.help"
          :loading="loading && !status" variant="report" class="summary-card" />
      </RouterLink>
    </section>
    <NCard v-if="!statusOnly" size="small" title="最近异常">
      <template #header-extra>
        <RouterLink :to="{ name: 'operations-incidents' }" custom v-slot="{ href, navigate }">
          <NButton tag="a" :href="href" size="small" @click="navigate">查看全部</NButton>
        </RouterLink>
      </template>
      <AppReportTable :items="overview?.recent ?? []" :columns="incidentColumns" :row-key="row => row.incident_id" :paginate="false" :loading="loading && !overview">
        <template #c0="{ row }">{{ stamp(row.last_seen_at) }}</template>
        <template #c1="{ row }"><NTag size="small" :type="tone(row.level)">{{ row.level }}</NTag></template>
        <template #c2="{ row }"><RouterLink class="inline-link" :to="{ name: 'operations-incidents', query: { incident: row.incident_id } }">{{ row.message }}</RouterLink></template>
        <template #c3="{ row }">{{ row.occurrences }}</template>
        <template #c4="{ row }">{{ incidentStatus[row.status] }}</template>
        <template #empty><NEmpty :description="overview?.recent ? '暂无异常事件' : '事件状态未知'" /></template>
      </AppReportTable>
    </NCard>
    <NCard v-if="!statusOnly && overview?.recurring?.length" size="small" title="持续性异常">
      <ul class="recurring">
        <li v-for="item in overview.recurring" :key="item.incident_id">
          <NTag size="small" :type="tone(item.level)">{{ item.level }}</NTag>
          <RouterLink class="inline-link" :to="{ name: 'operations-incidents', query: { incident: item.incident_id } }">{{ item.message }}</RouterLink>
          <span>{{ item.occurrences }} 次 · {{ stamp(item.last_seen_at) }}</span>
        </li>
      </ul>
    </NCard>
    <NSpin :show="loading && !status">
      <div class="runtime">
        <NCard size="small" title="运行角色">
          <template #header-extra>
            <NTag size="small" :type="stateTone(status?.postgres || 'unknown')">PostgreSQL {{ health[status?.postgres || 'unknown'] }}</NTag>
          </template>
          <p v-if="status" class="secondary">正常 Consumer {{ status.consumers_ready ?? '未知' }} / 期望 {{ status.consumers_expected }}</p>
          <AppReportTable :items="roleItems" :columns="roleColumns" :row-key="row => row.role + ':' + (row.instance_id || '')"
            :total="roleCount" :page="rolePage" :page-size="rolePageSize" @page="rolePage = $event">
            <template #c0="{ row }">{{ roles[row.role] }}</template>
            <template #c1="{ row }">{{ row.instance_id || (row.role === 'worker' ? '汇总' : '主实例') }}</template>
            <template #c2="{ row }"><NTag size="small" :type="stateTone(row.state)">{{ row.state === 'abnormal' ? roleDetails[row.detail] || health[row.state] : health[row.state] }}</NTag></template>
            <template #c3="{ row }">{{ stamp(row.heartbeat_at) }}</template>
            <template #c4="{ row }">{{ row.heartbeat_age_seconds == null ? '—' : row.heartbeat_age_seconds + ' 秒' }}</template>
            <template #c5="{ row }">{{ row.stalled_workers?.join('、') || '—' }}</template>
            <template #c6="{ row }">
              <span v-if="row.role === 'worker' && !row.instance_id">—</span>
              <NTag v-else size="small" :type="stateTone(row.configuration_state ?? 'unknown')">{{ configurationLabels[row.configuration_state ?? 'unknown'] }}</NTag>
            </template>
          </AppReportTable>
        </NCard>
        <div class="runtime-bottom">
          <NCard size="small" title="数据源端口监控">
            <p class="secondary">TCP 检测验证端口连通性。业务查询状态请查对应数据源的运行日志和异常事件。</p>
            <AppReportTable :items="status?.monitors ?? []" :columns="monitorColumns" :row-key="row => row.monitor_id" :paginate="false">
              <template #c0="{ row }">{{ row.name }}</template>
              <template #c1="{ row }"><NTag size="small" :type="stateTone(row.state)">{{ health[row.state] }}</NTag></template>
              <template #c2="{ row }">{{ stamp(row.last_checked_at) }}</template>
              <template #empty><NEmpty :description="status?.monitors ? '暂无已登记的监控目标' : '监控状态未知'" /></template>
            </AppReportTable>
          </NCard>
          <NCard size="small" title="队列与业务状态">
            <p class="secondary">报表、自动化和邮件失败数按近 24 小时统计。邮件结果待核对保留原状态。每组最多统计 10,001 条。</p>
            <AppReportTable :items="status?.work ?? []" :columns="workColumns" :row-key="row => row.group + ':' + row.status" :paginate="false">
              <template #c0="{ row }">{{ workGroup[row.group] }}</template>
              <template #c1="{ row }">{{ workStatus[row.status] || row.status }}</template>
              <template #c2="{ row }">{{ row.count }}</template>
              <template #empty><NEmpty :description="status?.work ? '没有活动或近期异常记录' : '队列状态未知'" /></template>
            </AppReportTable>
          </NCard>
        </div>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.operations, .runtime { display:grid; gap:var(--space-4); min-width:0; }
.runtime { grid-template-columns:minmax(0,1fr); }
.links { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); margin-top:var(--space-3); }
.secondary { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%, 176px),1fr)); gap:var(--space-3); }
.metric-link { display:flex; min-width:0; color:inherit; text-decoration:none; border-radius:var(--report-panel-radius); }
.summary-card { width:100%; min-height:114px; }
.inline-link { color:rgb(var(--primary-color)); text-decoration:none; }
.inline-link:hover { text-decoration:underline; }
.inline-link:focus-visible, .metric-link:focus-visible { outline:2px solid rgb(var(--primary-color)); outline-offset:2px; }
.metric-link:hover .summary-card { border-color:var(--kpi-color); }
.recurring { display:grid; gap:8px; margin:0; padding:0; list-style:none; }
.recurring li { display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; }
.recurring span { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.runtime-bottom { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
@media (max-width:820px) { .runtime-bottom { grid-template-columns:minmax(0,1fr); } }
</style>
