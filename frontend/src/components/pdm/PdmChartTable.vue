<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import ReportTrendPanel from '../ReportTrendPanel.vue'
import PdmCompletionChart from './PdmCompletionChart.vue'
import PdmSummaryTable from './PdmSummaryTable.vue'
import { pdmDetailCharts } from '../../utils/pdmReport'
import type { PdmPanelData } from '../../utils/pdmReport'

const props = defineProps<{ panel: PdmPanelData; loading: boolean }>()
const tableElement = ref<HTMLElement | null>(null)
const layout = shallowRef<{ height: number; groups: { category: string; top: number; height: number }[] }>({ height: 0, groups: [] })
const charts = computed(() => pdmDetailCharts(props.panel.table))
let observer: ResizeObserver | undefined

function measure() {
  const root = tableElement.value
  const table = root?.querySelector('table')
  if (!root || !table) return
  const bounds = root.getBoundingClientRect()
  const rows = Array.from(table.tBodies[0]?.rows || [])
  const groups = rows.flatMap((row, index) => {
    const cell = row.cells[0]
    if (cell?.scope !== 'rowgroup') return []
    const first = row.getBoundingClientRect(), last = rows[index + cell.rowSpan - 1].getBoundingClientRect()
    return [{ category: cell.textContent?.trim() || '', top: first.top - bounds.top, height: last.bottom - first.top }]
  })
  layout.value = { height: bounds.height, groups }
}
function chartStyle(category: string) {
  const group = layout.value.groups.find(item => item.category === category)
  // Split the 8px gap across neighbouring groups to preserve alignment with each table block's centre.
  return group ? { top: `${group.top + 4}px`, height: `${group.height - 8}px` } : undefined
}
onMounted(() => { observer = new ResizeObserver(measure); observer.observe(tableElement.value!); measure() })
watch(() => props.panel.table, measure, { flush: 'post' })
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <ReportTrendPanel class="pdm-chart-table" :class="{ 'pdm-chart-table--grouped': panel.table.group_label }" number="" :title="panel.table.scope_label"
    :subtitle="`${panel.filters.dateBasis === 'actual' ? '实际日期' : '计划日期'} · ${panel.filters.startDate} ～ ${panel.filters.endDate}`" fit-table-content :height="300" :has-data="true" :loading="loading"
    :chart-label="`${panel.table.title} · ${panel.table.scope_label}完成率对比`">
    <template #chart>
      <div v-if="panel.table.group_label" class="pdm-detail-charts" :style="{ height: `${layout.height}px` }">
        <section v-for="chart in charts" :key="chart.key" class="pdm-detail-chart" :style="chartStyle(chart.title)" :aria-label="`${chart.title} · ${panel.table.group_label}完成率`">
          <h4>{{ chart.title }}</h4><PdmCompletionChart :table="chart" detail />
        </section>
      </div>
      <PdmCompletionChart v-else :table="panel.table" />
    </template>
    <template #table><div ref="tableElement"><PdmSummaryTable :table="panel.table" :start-date="panel.filters.startDate" :end-date="panel.filters.endDate" :queried="true" /></div></template>
  </ReportTrendPanel>
</template>

<style scoped>
.pdm-chart-table :deep(.report-trend-panel__chart){align-self:center}
.pdm-chart-table--grouped :deep(.report-trend-panel__chart){height:auto;align-self:stretch;border:0;background:none}
/* Keep short project groups tall enough for their matching chart without adding a table scrollbar. */
.pdm-chart-table--grouped :deep(.pdm-summary__type){height:320px}
.pdm-detail-charts{position:relative;width:100%}
.pdm-detail-chart{position:absolute;left:0;right:0;display:grid;grid-template-rows:auto minmax(0,1fr);gap:var(--space-2);padding:var(--space-2);box-sizing:border-box;border:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}
.pdm-detail-chart h4{margin:0;font-size:var(--font-body);font-weight:500;color:var(--text-color-1);text-align:center}
.pdm-detail-chart :deep(.welding-chart){min-height:0}
</style>
