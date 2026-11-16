<script setup lang="ts">
import { computed, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { NAlert } from 'naive-ui'
import HomeKpiCard from '../components/HomeKpiCard.vue'
import PdmFilterBar from '../components/pdm/PdmFilterBar.vue'
import PdmSummaryTable from '../components/pdm/PdmSummaryTable.vue'
import { getPdmOptions, getPdmReport } from '../api/pdmStatistics'
import type { PdmStatisticsReport, PdmSummaryTable as SummaryTable } from '../api/generated/types.gen'
import { clonePdmFilters, createPdmFilters, pdmDocumentKinds, pdmPreferences } from '../utils/pdmFilters'
import type { PdmDocumentKind, PdmFilterOptions, PdmFilters } from '../utils/pdmFilters'
import { createStorage } from '../utils/storage'
import { session } from '../state/session'

const storage = createStorage<{ selection: unknown }>('local', `cutting:pdm:${session.user?.user_id || ''}:`)
const draft = ref(createPdmFilters(storage.get('selection')))
const applied = ref<PdmFilters | null>(null)
const options = ref<PdmFilterOptions>({})
const optionsStatus = shallowRef<'loading' | 'ready' | 'error'>('loading')
const optionsError = shallowRef('')
const report = shallowRef<PdmStatisticsReport | null>(null)
const querying = shallowRef(false)
const reportError = shallowRef('')
const retryOptions = shallowRef(0)
let reportController: AbortController | undefined
const ownerScopes = new Map<PdmDocumentKind, string>()

function scope(kind: PdmDocumentKind) {
  const branch = draft.value[kind]
  return JSON.stringify([branch.types.slice().sort(), branch.projects.slice().sort(), branch.professions.slice().sort()])
}
function filterKey(filters: PdmFilters) {
  const snapshot = clonePdmFilters(filters)
  snapshot.documentKinds.sort()
  for (const kind of snapshot.documentKinds) {
    const branch = snapshot[kind]
    for (const key of Object.keys(branch) as (keyof typeof branch)[]) branch[key]?.sort()
  }
  return JSON.stringify(snapshot)
}
const pending = computed(() => applied.value !== null && filterKey(draft.value) !== filterKey(applied.value))
watch(() => pdmPreferences(draft.value), value => storage.set('selection', value), { deep: true })

watch(() => JSON.stringify([draft.value.documentKinds, scope('equipment'), scope('drawing'), retryOptions.value]), async (_, __, onCleanup) => {
  const controller = new AbortController()
  let active = true
  onCleanup(() => { active = false; controller.abort() })
  optionsStatus.value = 'loading'
  optionsError.value = ''
  try {
    const loaded = await Promise.all(draft.value.documentKinds.map(async kind => ({ kind, scope: scope(kind), value: await getPdmOptions(kind, clonePdmFilters(draft.value)[kind], controller.signal) })))
    if (!active) return
    const next = clonePdmFilters(draft.value)
    const available = { ...options.value }
    for (const item of loaded) {
      available[item.kind] = item.value
      const branch = next[item.kind]
      branch.projects = branch.projects.filter(id => item.value.projects?.some(option => option.value === id))
      branch.professions = branch.professions.filter(id => item.value.professions?.some(option => option.value === id))
      const people = item.value.responsible_people?.map(option => option.value) || []
      branch.responsiblePeople = ownerScopes.get(item.kind) === item.scope && branch.responsiblePeople !== null
        ? branch.responsiblePeople.filter(value => people.includes(value)) : people
      ownerScopes.set(item.kind, item.scope)
    }
    options.value = available
    draft.value = next
    optionsStatus.value = 'ready'
  } catch (error) {
    if (!active) return
    optionsError.value = error instanceof Error ? error.message : String(error)
    optionsStatus.value = 'error'
  }
}, { immediate: true })

async function query(filters: PdmFilters) {
  reportController?.abort()
  const controller = new AbortController()
  reportController = controller
  querying.value = true
  reportError.value = ''
  try {
    const result = await getPdmReport(filters, controller.signal)
    if (controller !== reportController) return
    report.value = result
    applied.value = clonePdmFilters(filters)
  } catch (error) {
    if (controller !== reportController || controller.signal.aborted) return
    reportError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (controller === reportController) querying.value = false
  }
}
onBeforeUnmount(() => { reportController?.abort(); reportController = undefined })
const tables = computed<SummaryTable[]>(() => report.value?.tables || draft.value.documentKinds.map(kind => ({
  key: `${kind}:summary`, kind, title: kind === 'equipment' ? '设备资料计划完成汇总表' : '详设图纸计划完成汇总表', scope_label: '所有项目', group_label: null,
  rows: [], totals: { total: 0, completed: 0, on_time: 0, total_rate: null, on_time_rate: null },
})))
const cards = computed(() => pdmDocumentKinds.map(kind => ({ ...kind, totals: report.value?.summaries[kind.value] })))
</script>

<template>
  <section class="pdm-page">
    <header class="pdm-page__heading"><h1>PDM 资料统计</h1></header>
    <div class="pdm-page__cards">
      <HomeKpiCard v-for="card in cards" :key="card.value" :title="`${card.label}完成率`" :value="card.totals?.total_rate ?? null" unit="%" :precision="2" variant="report" :icon="card.value === 'equipment' ? 'briefcase' : 'file'" :color="card.value === 'equipment' ? 'rgb(var(--info-color))' : 'rgb(var(--success-color))'" :loading="querying && !report"
        :trend-label="card.totals ? `已完成 ${card.totals.completed} / 总数 ${card.totals.total} · 按计划完成 ${card.totals.on_time}` : '选择时间段后查询'" />
    </div>
    <PdmFilterBar v-model="draft" :options="options" :status="optionsStatus" :error="optionsError" :pending="pending" :querying="querying" @submit="query" @retry="retryOptions++" />
    <p class="pdm-page__basis">有计划日期时按计划日期筛选；仅有实际日期时按实际日期筛选并计为按时完成。两个日期都为空的记录，每个区间均计入总数和未完成数。完成状态截至所选结束日判断。</p>
    <NAlert v-if="reportError" type="error" title="统计查询失败">{{ reportError }}<span v-if="report">，下方保留上一次成功查询结果。</span></NAlert>
    <div class="pdm-page__tables" :aria-busy="querying">
      <PdmSummaryTable v-for="table in tables" :key="table.key" :table="table" :start-date="report?.start_date || ''" :end-date="report?.end_date || ''" :queried="report !== null" />
    </div>
  </section>
</template>

<style scoped>
.pdm-page{display:grid;gap:var(--space-4);min-width:1000px;padding-block:var(--space-4)}
.pdm-page__heading h1{margin:0;font-size:var(--font-page-title);font-weight:600;color:var(--text-color-1)}
.pdm-page__cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-4)}
.pdm-page__cards :deep(.home-kpi--report){min-height:180px;padding:var(--space-6);--font-home-kpi-value:30px}
.pdm-page__tables{display:grid;gap:var(--space-6)}
.pdm-page__basis{margin:0;color:var(--text-color-3);font-size:var(--font-auxiliary)}
</style>
