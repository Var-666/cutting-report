<script setup lang="ts">
import { computed } from 'vue'
import { useThemeVars } from 'naive-ui'
import type { EChartsOption } from 'echarts'
import type { PdmSummaryTable } from '../../api/generated/types.gen'
import { formatPdmRate, pdmChartRows, pdmInitialProjectCount } from '../../utils/pdmReport'
import WeldingChart from '../WeldingChart.vue'

const props = defineProps<{ table: PdmSummaryTable }>()
const theme = useThemeVars()
const colors = computed(() => [
  theme.value.primaryColor, theme.value.successColor, '#2da9fa', theme.value.warningColor, '#f267aa',
  '#17c6cc', '#ae7aff', theme.value.errorColor, '#527cf6', '#90da54', '#ff9157', '#d391f7',
])
const rows = computed(() => pdmChartRows(props.table, Boolean(props.table.group_label)))
const preview = computed(() => props.table.group_label === '项目' && rows.value.length > pdmInitialProjectCount)
const displayed = computed(() => preview.value ? rows.value.slice(0, pdmInitialProjectCount) : rows.value)
const rates = computed(() => {
  if (!preview.value) return props.table.totals
  const total = displayed.value.reduce((sum, row) => sum + row.total, 0)
  return {
    on_time_rate: total ? 100 * displayed.value.reduce((sum, row) => sum + row.on_time, 0) / total : null,
    total_rate: total ? 100 * displayed.value.reduce((sum, row) => sum + row.completed, 0) / total : null,
  }
})
const slices = computed(() => {
  const { group_label: group, totals } = props.table
  const data = displayed.value.map(row => ({
    key: group ? row.group_id || row.group_name : row.category,
    name: group ? row.group_name ?? '' : row.category,
    on_time: row.on_time, completed: row.completed,
  }))
  if (group && !preview.value) {
    const onTime = totals.on_time - data.reduce((sum, row) => sum + row.on_time, 0)
    const completed = totals.completed - data.reduce((sum, row) => sum + row.completed, 0)
    if (onTime > 0 || completed > 0) data.push({ key: '未分配', name: '未分配', on_time: onTime, completed })
  }
  return data.map((row, index) => {
    const color = row.key === '未分配' ? '#cbd5e1' : colors.value[index] ?? `hsl(${(index * 137) % 360}, 82%, 64%)`
    return { ...row, color }
  })
})
const metrics = computed(() => ([
  { label: '按时完成率', rate: rates.value.on_time_rate, field: 'on_time' },
  { label: '总完成率', rate: rates.value.total_rate, field: 'completed' },
] as const).map(metric => {
  const data = slices.value.filter(row => row[metric.field] > 0).map(row => ({
    name: row.name, value: row[metric.field], itemStyle: { color: row.color },
  }))
  const sliceTotal = data.reduce((sum, item) => sum + item.value, 0)
  const option: EChartsOption = {
    tooltip: {
      trigger: 'item', renderMode: 'html', appendTo: 'body',
      position: (_point, _data, _element, _sector, { viewSize }) => ({
        right: 0, bottom: viewSize[1] + 28,
      }),
      extraCssText: 'max-width:min(240px,calc(100vw - 24px));white-space:normal;overflow-wrap:anywhere;',
      valueFormatter: value => `${value} 份（${formatPdmRate(100 * Number(value) / sliceTotal)}）`,
    },
    series: [{
      type: 'pie', radius: '64%', stillShowZeroSum: false,
      emptyCircleStyle: { color: theme.value.dividerColor },
      itemStyle: { borderColor: theme.value.cardColor, borderWidth: 2 },
      cursor: 'default',
      emphasis: { scale: false },
      label: { show: false },
      labelLine: { show: false },
      data,
    }],
  }
  return { label: metric.label, rate: formatPdmRate(metric.rate), option }
}))
</script>

<template>
  <div class="pdm-completion-pies">
    <p v-if="preview" class="pdm-completion-pies__scope">前 {{ pdmInitialProjectCount }} 个项目</p>
    <section v-for="metric in metrics" :key="metric.label" class="pdm-completion-pies__metric">
      <h5>{{ metric.label }} <strong>{{ metric.rate }}</strong></h5>
      <div class="pdm-completion-pies__plot">
        <WeldingChart :option="metric.option" :label="`${metric.label} ${metric.rate}，按${table.group_label || '资料类型'}分布`" />
      </div>
    </section>
    <ul v-if="table.group_label !== '项目'" class="pdm-completion-pies__legend" :aria-label="`${table.group_label || '资料类型'}图例`">
      <li v-for="slice in slices" :key="slice.key" :title="`按时完成 ${slice.on_time} 份，已完成 ${slice.completed} 份`">
        <span aria-hidden="true" :style="{ backgroundColor: slice.color }" />{{ slice.name }}
      </li>
    </ul>
  </div>
</template>

<style scoped>
.pdm-completion-pies{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-2);min-width:0}
.pdm-completion-pies__scope{grid-column:1/-1;text-align:center;margin:0;color:var(--text-color-2);font-size:var(--font-auxiliary)}
.pdm-completion-pies__metric h5{margin:0;text-align:center;color:var(--text-color-2);font-size:var(--font-body);font-weight:500}
.pdm-completion-pies__plot{height:148px}
.pdm-completion-pies__plot :deep(.welding-chart){min-height:0}
.pdm-completion-pies__metric strong{margin-inline-start:var(--space-1);color:var(--text-color-1);font-variant-numeric:tabular-nums;font-weight:700}
.pdm-completion-pies__legend{grid-column:1/-1;display:flex;flex-wrap:wrap;justify-content:center;gap:var(--space-1) var(--space-2);margin:0;padding:0;list-style:none}
.pdm-completion-pies__legend li{display:inline-flex;align-items:center;gap:var(--space-1);padding:var(--space-1);color:var(--text-color-1);font-size:var(--font-auxiliary)}
.pdm-completion-pies__legend span{width:9px;height:9px;border-radius:50%}
</style>
