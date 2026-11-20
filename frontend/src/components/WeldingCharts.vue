<script setup lang="ts">
import type { DataTableBaseColumn } from 'naive-ui'
import type { DataRow } from '../types/api'
import type { WeldingReport } from '../types/welding'
import { computed } from 'vue'
import { NEmpty } from 'naive-ui'
import ReportTrendPanel from './ReportTrendPanel.vue'
import AppReportTable from './AppReportTable.vue'
import WeldingChart from './WeldingChart.vue'
import { useReportChartTheme } from '../composables/useReportChartTheme'
import { weldingDisplay, weldingProcessRows } from '../utils/weldingReport'
import { weldingTrendOption, weldingLengthOption, weldingProcessOption } from '../utils/weldingChartOptions'

const props = defineProps<{ report: WeldingReport; kind: string; loading?: boolean }>()
const { colors, vars } = useReportChartTheme(() => {})
const daily = computed(() => [...props.report.daily].sort((a, b) => a.production_date.localeCompare(b.production_date)))
const devices = computed(() => [...props.report.device_distribution].sort((a, b) => b.weld_length_m - a.weld_length_m))
const projects = computed(() => [...props.report.projects].sort((a, b) => (b.cumulative_actual_length_m ?? -1) - (a.cumulative_actual_length_m ?? -1)))
const processes = computed(() => weldingProcessRows(props.report.summary))
const palette = computed(() => [colors.value.primary, colors.value.secondary, vars.value.errorColor, colors.value.accent, vars.value.textColor3])
const panel = computed(() => {
  if (props.kind === 'parts') return { title: '项目工件分布', subtitle: '归属唯一且存在有效区间作业的工件累计产出', rows: projects.value,
    option: weldingLengthOption(projects.value.map(row => row.project_no ?? '未填写项目'), projects.value.map(row => row.cumulative_actual_length_m), colors.value.primary, '工件累计实际长度'),
    fields: [['project_no', '项目编号'], ['part_count', '工件数'], ['cumulative_actual_length_m', '累计实际长度(m)']] }
  if (props.kind === 'equipment') return { title: '设备产出对比', rows: devices.value,
    option: weldingLengthOption(devices.value.map(row => `设备 ${row.device_no ?? '未记录'}`), devices.value.map(row => row.weld_length_m), colors.value.primary),
    fields: [] }
  if (props.kind === 'jobs') return { title: '焊接工艺结构', subtitle: '按有效焊接长度统计，未知工艺独立保留', rows: processes.value,
    option: weldingProcessOption(processes.value, palette.value), fields: [['name', '焊接工艺'], ['length', '焊接长度(m)']] }
  return { title: '每日焊接趋势', subtitle: '每日长度与作业次数；区间工件数按流水号独立去重', rows: daily.value,
    option: weldingTrendOption(daily.value, colors.value.primary, colors.value.secondary), fields: [] }
})
function columns(fields: string[][]): DataTableBaseColumn<object>[] {
  return fields.map(([key, title]) => ({ key, title, width: key === 'production_date' || key === 'project_no' ? 140 : 120,
    align: /length|hours|count|output/.test(key) ? 'right' : 'left', ellipsis: { tooltip: true }, render: row => weldingDisplay((row as DataRow)[key], key) }))
}
function rowKey(row: object) {
  const data = row as DataRow
  return String(data.production_date ?? data.project_no ?? data.key ?? `${data.device_no}:${data.robot_no}`)
}
</script>

<template>
  <div class="welding-statistics">
    <ReportTrendPanel :title="panel.title" :subtitle="panel.subtitle" :chart-label="panel.title" :show-table="panel.fields.length > 0" :height="245" :loading="loading" :empty="!panel.rows.length" :has-data="panel.rows.length > 0">
      <template #chart><div v-if="kind === 'jobs' && !processes.some(row => row.length !== null && row.length > 0)" class="welding-chart-empty"><NEmpty description="暂无有效工艺长度数据" /></div><WeldingChart v-else :option="panel.option" :label="panel.title" /></template>
      <template #table><AppReportTable :columns="columns(panel.fields)" :items="panel.rows" :row-key="rowKey" :paginate="false" :max-height="245" /></template>
    </ReportTrendPanel>
  </div>
</template>

<style scoped>
.welding-statistics { display:flex; flex-direction:column; gap:24px; min-width:0; }
.welding-statistics :deep(.report-trend-panel) { margin-bottom:0; border:1px solid var(--theme-border-color); box-shadow:var(--theme-shadow); }
.welding-statistics :deep(.report-trend-panel__chart),.welding-statistics :deep(.report-trend-panel__table) { border-radius:6px; }
.welding-chart-empty { height:100%; display:flex; align-items:center; justify-content:center; }
</style>
