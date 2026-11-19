<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, shallowRef } from 'vue'
import { NAlert, NButton } from 'naive-ui'
import PdmCompletionCards from '../components/pdm/PdmCompletionCards.vue'
import PdmReportPanel from '../components/pdm/PdmReportPanel.vue'
import ReportPanelState from '../components/ReportPanelState.vue'
import ReportDataStatus from '../components/ReportDataStatus.vue'
import { getPdmOverview, getPdmReport } from '../api/pdmStatistics'
import { pollReportUpdates, type ReportReadMode } from '../api/reportRequests'
import type { PdmOverviewReport, PdmStatisticsReport } from '../api/generated/types.gen'
import { createPdmFilters, pdmDocumentKinds, pdmPreferences } from '../utils/pdmFilters'
import type { PdmDocumentKind, PdmFilters } from '../utils/pdmFilters'
import { pdmReportRegion, pdmReportSections } from '../utils/pdmReport'
import type { PdmReportRegion } from '../utils/pdmReport'
import { createStorage } from '../utils/storage'
import { session } from '../state/session'

const storage = createStorage<{ selection: unknown }>('local', `cutting:pdm:${session.user?.user_id || ''}:`)
const initialFilters = createPdmFilters(storage.get('selection'))
const overview = shallowRef<PdmOverviewReport | null>(null)
const overviewLoading = shallowRef(true)
const overviewError = shallowRef('')
const regions = shallowRef<PdmReportRegion[]>([])
const panelsLoading = shallowRef(true)
const panelsError = shallowRef('')
const activeKind = shallowRef<PdmDocumentKind>('equipment')
const visibleRegions = computed(() => regions.value.filter(region => region.section.kind === activeKind.value))
const activeKindLabel = computed(() => pdmDocumentKinds.find(kind => kind.value === activeKind.value)?.label)
let overviewController: AbortController | undefined
let panelsController: AbortController | undefined

function updateRegion(replacement: PdmReportRegion) {
  regions.value = regions.value.map(region => region.section.key === replacement.section.key ? replacement : region)
}
function remember(filters: PdmFilters) {
  const saved = createPdmFilters(storage.get('selection'))
  for (const kind of filters.documentKinds) {
    saved[kind].projects = [...filters[kind].projects]
    saved[kind].professions = [...filters[kind].professions]
  }
  storage.set('selection', pdmPreferences(saved))
}
async function loadPanels(mode: ReportReadMode = 'read') {
  panelsController?.abort()
  const controller = new AbortController()
  panelsController = controller
  panelsLoading.value = true
  panelsError.value = ''
  try {
    const result = await getPdmReport(initialFilters, controller.signal, { mode })
    if (controller !== panelsController) return
    const targets = new Map(regions.value.map(region => [region.section.key, region]))
    function apply(value: PdmStatisticsReport) {
      const incoming = pdmReportSections.map(section => pdmReportRegion(section, initialFilters, value))
      regions.value = regions.value.length ? regions.value.map(region => {
        const next = incoming.find(item => item.section.key === region.section.key)!
        if (targets.get(region.section.key) !== region || JSON.stringify(region.filters) !== JSON.stringify(next.filters)) return region
        targets.set(region.section.key, next)
        return next
      }) : incoming
      if (!targets.size) regions.value.forEach(region => targets.set(region.section.key, region))
    }
    apply(result)
    void pollReportUpdates(result, () => getPdmReport(initialFilters, controller.signal, { mode: 'poll' }), value => {
      if (controller === panelsController) apply(value)
    }, controller.signal).catch(error => {
      if (controller === panelsController && !controller.signal.aborted) panelsError.value = error instanceof Error ? error.message : String(error)
    })
  } catch (error) {
    if (controller !== panelsController || controller.signal.aborted) return
    panelsError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (controller === panelsController) panelsLoading.value = false
  }
}
async function loadOverview(mode: ReportReadMode = 'read') {
  overviewController?.abort()
  const controller = new AbortController()
  overviewController = controller
  overviewLoading.value = true
  overviewError.value = ''
  try {
    const result = await getPdmOverview(controller.signal, { mode })
    if (controller === overviewController) overview.value = result
    void pollReportUpdates(result, () => getPdmOverview(controller.signal, { mode: 'poll' }), value => {
      if (controller === overviewController) overview.value = value
    }, controller.signal).catch(error => {
      if (controller === overviewController && !controller.signal.aborted) overviewError.value = error instanceof Error ? error.message : String(error)
    })
  } catch (error) {
    if (controller !== overviewController || controller.signal.aborted) return
    overviewError.value = error instanceof Error ? error.message : String(error)
  } finally {
    if (controller === overviewController) overviewLoading.value = false
  }
}
onMounted(() => { void loadOverview(); void loadPanels() })
onBeforeUnmount(() => {
  panelsController?.abort(); panelsController = undefined
  overviewController?.abort(); overviewController = undefined
})
</script>

<template>
  <section class="pdm-page">
    <header class="page-heading"><h1 class="page-heading__title">PDM 资料统计</h1></header>
    <PdmCompletionCards :overview="overview" :loading="overviewLoading" :active-kind="activeKind" @select="activeKind = $event" />
    <ReportDataStatus :meta="overview?.meta" :busy="overviewLoading" @refresh="loadOverview('refresh')" />
    <NAlert v-if="overviewError" type="error" title="全部数据统计加载失败">{{ overviewError }}<NButton size="small" :loading="overviewLoading" @click="loadOverview('refresh')">重试</NButton></NAlert>
    <ReportPanelState v-if="panelsLoading && !regions.length" type="loading" title="正在读取当月统计数据" message="各表加载后可分别打开筛选。" />
    <ReportPanelState v-else-if="panelsError && !regions.length" type="error" title="统计查询失败" :message="panelsError" action-label="重新查询" @action="loadPanels('refresh')" />
    <NAlert v-if="panelsError && regions.length" type="error" title="统计更新失败">{{ panelsError }}，原表格与图表已保留。<NButton size="small" @click="loadPanels('refresh')">重试</NButton></NAlert>
    <section v-if="regions.length" class="pdm-page__tables" :aria-label="`${activeKindLabel}统计`">
      <h2>{{ activeKindLabel }}统计</h2>
      <PdmReportPanel v-for="region in visibleRegions" :key="region.section.key" :region="region" @update="updateRegion" @remember="remember" />
    </section>
  </section>
</template>

<style scoped>
.pdm-page{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-4);padding-block:var(--space-4)}
.pdm-page__tables{display:grid;grid-template-columns:minmax(0,1fr);gap:var(--space-6)}
.pdm-page__tables h2{margin:0;color:var(--text-color-1);font-size:var(--font-section-title);font-weight:600}
</style>
