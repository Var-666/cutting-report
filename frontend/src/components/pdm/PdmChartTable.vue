<script setup lang="ts">
import { computed, shallowRef, useId } from 'vue'
import { NModal } from 'naive-ui'
import PdmCompletionChart from './PdmCompletionChart.vue'
import PdmCompletionPies from './PdmCompletionPies.vue'
import PdmSummaryTable from './PdmSummaryTable.vue'
import { pdmChartRows, pdmCompletionChartWidth, pdmDetailCharts, pdmInitialProjectCount } from '../../utils/pdmReport'
import type { PdmPanelData } from '../../utils/pdmReport'

const props = defineProps<{ panel: PdmPanelData; loading: boolean }>()
const titleId = useId()
const table = computed(() => props.panel.table)
const projectSelection = computed(() => table.value.key === 'equipment:all:project')
const checkedProjects = shallowRef(new Set<string>())
const selectedProjects = computed(() => new Set(table.value.rows.flatMap(row =>
  row.group_id && checkedProjects.value.has(row.group_id) ? [row.group_id] : [],
)))
const categories = computed(() => pdmDetailCharts(table.value).map(category => {
  if (category.group_label !== '项目') return { category, chart: category }
  const selected = projectSelection.value ? selectedProjects.value : undefined
  const rows = selected?.size
    ? category.rows.filter(row => row.group_id && selected.has(row.group_id))
    : category.rows.slice(0, pdmInitialProjectCount)
  return { category, chart: { ...category, rows } }
}))
const period = computed(() => `${props.panel.filters.dateBasis === 'actual' ? '实际日期' : '计划日期'} · ${props.panel.filters.startDate} ～ ${props.panel.filters.endDate}`)
const openDetail = shallowRef<{ category?: string } | null>(null)
const detailTable = computed(() => {
  const detail = openDetail.value
  if (!detail) return
  const { category } = detail
  if (!category) return table.value
  const totals = pdmChartRows(table.value).find(row => row.category === category)
  if (totals) return { ...table.value, rows: table.value.rows.filter(row => row.category === category), totals }
})
const dialogTitle = computed(() => {
  const detail = openDetail.value
  if (!detail) return ''
  return detail.category ? `${table.value.scope_label} · ${detail.category} · 完整明细` : `${table.value.scope_label} · 全部明细`
})
const detailChart = computed(() => categories.value.find(item => item.category.title === openDetail.value?.category)?.chart)
function openDetails(category?: string) {
  openDetail.value = { category }
}
function toggleProject(id: string) {
  const next = new Set(checkedProjects.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  checkedProjects.value = next
}
</script>

<template>
  <section class="panel pdm-chart-table" :aria-labelledby="titleId">
    <header class="panel-title pdm-chart-table__heading">
      <div>
        <h4 :id="titleId">{{ table.scope_label }}</h4><small>{{ period }}</small>
        <small v-if="projectSelection && selectedProjects.size">柱状图显示已选 {{ selectedProjects.size }} 个项目</small>
      </div>
      <span v-if="loading" role="status" class="pdm-chart-table__updating">正在更新</span>
    </header>
    <div class="pdm-chart-table__body" :class="{ 'pdm-chart-table__body--summary': !table.group_label }">
      <template v-if="!table.group_label">
        <div class="pdm-chart-table__chart"><PdmCompletionChart :table="table" opens-details @open-details="openDetails()" /></div>
        <PdmCompletionPies :table="table" />
      </template>
      <template v-else>
        <section v-for="{ category, chart } in categories" :key="category.key" class="pdm-chart-table__chart pdm-chart-table__group" :aria-label="`${category.title}图表`">
          <h5>{{ category.title }}</h5>
          <PdmCompletionChart :table="chart" detail opens-details @open-details="openDetails(category.title)" />
          <PdmCompletionPies :table="category" />
        </section>
      </template>
    </div>
    <NModal :show="Boolean(detailTable)" preset="card" :title="dialogTitle" :aria-label="dialogTitle"
      :style="{ width: projectSelection ? 'min(1200px, calc(100vw - 24px))' : 'min(940px, calc(100vw - 24px))' }"
      :content-style="{ maxHeight: 'calc(100dvh - 128px)', overflowY: 'auto' }" @update:show="show => { if (!show) openDetail = null }">
      <div v-if="detailTable" class="pdm-chart-table__details" :class="{ 'pdm-chart-table__details--project': projectSelection }">
        <div v-if="projectSelection && detailChart" class="pdm-chart-table__chart pdm-chart-table__detail-chart"
          :style="{ flexBasis: `${pdmCompletionChartWidth(detailChart.rows.length) + 2}px` }">
          <h5>{{ selectedProjects.size ? `已选 ${selectedProjects.size} 个项目` : `默认显示前 ${pdmInitialProjectCount} 个项目` }}</h5>
          <PdmCompletionChart :table="detailChart" detail />
        </div>
        <PdmSummaryTable :table="detailTable" :start-date="panel.filters.startDate" :end-date="panel.filters.endDate" :queried="true"
          :selected-projects="projectSelection ? selectedProjects : undefined" @select="toggleProject" />
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.pdm-chart-table{margin:0;min-width:0;overflow:hidden}
.pdm-chart-table__heading{min-height:48px;gap:var(--space-2);padding:var(--space-2) var(--space-4);flex-wrap:wrap}
.pdm-chart-table__heading > div{gap:var(--space-1) var(--space-2);flex-wrap:wrap}
.pdm-chart-table__heading h4,.pdm-chart-table__chart > h5{margin:0;color:var(--text-color-1);font-size:var(--font-body);font-weight:600}
.pdm-chart-table__heading small,.pdm-chart-table__heading .pdm-chart-table__updating{color:var(--text-color-2);font-size:var(--font-auxiliary)}
.pdm-chart-table__body{display:grid;gap:var(--space-4);padding:var(--space-4)}
.pdm-chart-table__body--summary,.pdm-chart-table__group{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:var(--space-4);align-items:center}
.pdm-chart-table__chart{min-width:0;border:1px solid var(--theme-border-color);border-radius:var(--card-radius);overflow:hidden}
.pdm-chart-table__chart > h5{padding:var(--space-2) var(--space-3)}
.pdm-chart-table__group{row-gap:0;padding:var(--space-3)}
.pdm-chart-table__group > h5{grid-column:1/-1;padding:0 0 var(--space-2)}
.pdm-chart-table__details--project{display:flex;flex-wrap:wrap;gap:var(--space-4);align-items:flex-start}
.pdm-chart-table__detail-chart{flex-grow:1;max-width:100%}
.pdm-chart-table__details--project > :deep(.pdm-summary-wrap){flex:1 1 568px;max-width:100%}
@media (max-width:900px){
  .pdm-chart-table__body--summary,.pdm-chart-table__group{grid-template-columns:minmax(0,1fr)}
}
</style>
