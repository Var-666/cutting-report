<script setup lang="ts">
import type { WeldingReport, WeldingReportKind } from '../types/welding'
import { computed } from 'vue'
import { NCard } from 'naive-ui'
import HomeKpiCard from './HomeKpiCard.vue'
import { weldingKpis, weldingMetricSeries } from '../utils/weldingReport'

const props = defineProps<{ kind: WeldingReportKind; report: WeldingReport | null; loading: boolean }>()
const cards = computed(() => weldingKpis(props.kind, props.report))
const icons: Record<string, string> = { weld_length_m: 'ruler', part_actual_length_m: 'ruler', job_count: 'tasks', involved_part_count: 'grid', working_hours: 'clock', part_row_count: 'file', ambiguous_part_count: 'search', device_count: 'factory', robot_station_count: 'grid', output_per_hour: 'trend' }
const colors = ['primary', 'success', 'info', 'warning']
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
  <section class="welding-kpis" aria-label="焊接生产指标" :aria-busy="loading">
    <NCard v-for="(card, index) in cards" :key="card.key" :bordered="false" class="card-wrapper" content-style="padding: 0">
      <HomeKpiCard :title="card.title" :icon="icons[card.key]" :color="`rgb(var(--${colors[index]}-color))`" :value="card.value" :unit="card.unit" :precision="['m', 'h', 'm/h'].includes(card.unit) ? 2 : 0" :loading="loading && !report" :status="card.pending ? '口径待核实' : ''" :trend-label="note(card.key)" :series="weldingMetricSeries(report, card.key).some(value => value !== null) ? weldingMetricSeries(report, card.key) : []" />
    </NCard>
  </section>
</template>

<style scoped>
.welding-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;min-width:0}
@media(max-width:1100px){.welding-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:560px){.welding-kpis{grid-template-columns:1fr}}
</style>
