<script setup lang="ts">
import type { WeldingReport, WeldingReportKind } from '../types/welding'
import { computed } from 'vue'
import { NCard } from 'naive-ui'
import HomeKpiCard from './HomeKpiCard.vue'
import { weldingKpis, weldingMetricSeries } from '../utils/weldingReport'

const props = defineProps<{ kind: WeldingReportKind; report: WeldingReport | null; loading: boolean }>()
const cards = computed(() => weldingKpis(props.kind, props.report))
const icons: Record<string, string> = { weld_length_m: 'ruler', part_actual_length_m: 'ruler', job_count: 'tasks', involved_part_count: 'grid', working_hours: 'clock', part_row_count: 'file', ambiguous_part_count: 'search', device_count: 'factory', robot_station_count: 'grid', output_per_hour: 'trend' }
const colors: Record<string, string> = { weld_length_m: 'success', part_actual_length_m: 'success', job_count: 'primary', involved_part_count: 'info', working_hours: 'warning', part_row_count: 'primary', ambiguous_part_count: 'warning', device_count: 'primary', robot_station_count: 'info', output_per_hour: 'warning' }
function note(key: string) {
  if (key === 'involved_part_count') return '按区间流水号去重，日数不可累加'
  if (key === 'part_actual_length_m') return '工件累计值；归属不明确时待核实'
  if (key === 'part_row_count') return '当前筛选范围内的工件记录'
  if (key === 'ambiguous_part_count') return '参与流水号存在多条工件归属'
  if (key === 'robot_station_count') return '按设备与机器人组合去重'
  if (key === 'output_per_hour') return '仅使用长度、用时均有效的作业'
  return '近期每日趋势'
}
</script>

<template>
  <NCard :bordered="false" class="card-wrapper welding-kpi-panel" content-style="padding: 0">
  <section class="welding-kpis" :class="{ 'welding-kpis--report': kind !== 'dashboard' }" aria-label="焊接生产指标" :aria-busy="loading">
    <div v-for="card in cards" :key="card.key" class="welding-kpi">
      <HomeKpiCard class="welding-kpi-content" :title="card.title" :icon="icons[card.key]" :color="`rgb(var(--${colors[card.key]}-color))`" :value="card.value" :unit="card.unit" :precision="['m', 'h', 'm/h'].includes(card.unit) ? 2 : 0" :loading="loading && !report" :status="card.pending ? '口径待核实' : ''" :trend-label="note(card.key)" :series="weldingMetricSeries(report, card.key).some(value => value !== null) ? weldingMetricSeries(report, card.key) : []" />
    </div>
  </section>
  </NCard>
</template>

<style scoped>
.welding-kpi-panel { container:welding-kpis / inline-size; }
.welding-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); min-width:0; }
.welding-kpi { min-width:0; border-right:1px solid var(--theme-divider-color); }
.welding-kpi:last-child { border-right:0; }
.welding-kpis--report { --font-home-kpi-value:var(--font-kpi-value); }
.welding-kpis--report .welding-kpi-content { min-height:152px; }
@container welding-kpis (max-width:900px) {
  .welding-kpis { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .welding-kpi:nth-child(2) { border-right:0; }
  .welding-kpi:nth-child(-n+2) { border-bottom:1px solid var(--theme-divider-color); }
}
@container welding-kpis (max-width:480px) {
  .welding-kpis { grid-template-columns:1fr; }
  .welding-kpi { border-right:0; border-bottom:1px solid var(--theme-divider-color); }
  .welding-kpi:last-child { border-bottom:0; }
}
</style>
