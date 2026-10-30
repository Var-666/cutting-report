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

const props = defineProps<{ report: WeldingReport; kind: string }>()
const { colors, vars } = useReportChartTheme(() => {})
const daily = computed(() => [...props.report.daily].sort((a, b) => a.production_date.localeCompare(b.production_date)))
const devices = computed(() => [...props.report.device_distribution].sort((a, b) => b.weld_length_m - a.weld_length_m))
const stations = computed(() => [...props.report.equipment].sort((a, b) => b.weld_length_m - a.weld_length_m))
const projects = computed(() => [...props.report.projects].sort((a, b) => (b.cumulative_actual_length_m ?? -1) - (a.cumulative_actual_length_m ?? -1)))
const processes = computed(() => weldingProcessRows(props.report.summary))
const palette = computed(() => [colors.value.primary, colors.value.secondary, vars.value.errorColor, colors.value.accent, vars.value.textColor3])
const first = computed(() => {
  if (props.kind === 'parts') return { title: '项目工件分布', subtitle: '归属唯一且存在有效区间作业的工件累计产出', rows: projects.value,
    option: weldingLengthOption(projects.value.map(row => row.project_no ?? '未填写项目'), projects.value.map(row => row.cumulative_actual_length_m), colors.value.primary, '工件累计实际长度'),
    fields: [['project_no', '项目编号'], ['part_count', '工件数'], ['cumulative_actual_length_m', '累计实际长度(m)']] }
  if (props.kind === 'equipment') return { title: '设备产出对比', subtitle: '按设备汇总有效焊接产出', rows: devices.value,
    option: weldingLengthOption(devices.value.map(row => `设备 ${row.device_no ?? '未记录'}`), devices.value.map(row => row.weld_length_m), colors.value.primary),
    fields: [['device_no', '设备'], ['involved_part_count', '参与工件数'], ['job_count', '作业次数'], ['weld_length_m', '焊接长度(m)'], ['working_hours', '作业用时(h)']] }
  return { title: props.kind === 'jobs' ? '焊接作业趋势' : '每日焊接趋势', subtitle: '每日长度与作业次数；区间工件数按流水号独立去重', rows: daily.value,
    option: weldingTrendOption(daily.value, colors.value.primary, colors.value.secondary),
    fields: [['production_date', '日期'], ['job_count', '作业次数'], ['involved_part_count', '参与工件数'], ['weld_length_m', '焊接长度(m)'], ['working_hours', '作业用时(h)']] }
})
const second = computed(() => {
  if (props.kind === 'equipment') return { title: '机器人工位分析', subtitle: '以设备与机器人组合识别工位', rows: stations.value,
    option: weldingLengthOption(stations.value.map(row => `${row.device_no ?? '未记录'} / ${row.robot_no ?? '未记录'}`), stations.value.map(row => row.weld_length_m), colors.value.secondary),
    fields: [['device_no', '设备'], ['robot_no', '机器人'], ['job_count', '作业次数'], ['weld_length_m', '焊接长度(m)'], ['working_hours', '作业用时(h)'], ['output_per_hour', '单位用时产出(m/h)']] }
  if (props.kind === 'jobs') return { title: '焊接工艺结构', subtitle: '按有效焊接长度统计，未知工艺独立保留', rows: processes.value,
    option: weldingProcessOption(processes.value, palette.value), fields: [['name', '焊接工艺'], ['length', '焊接长度(m)']] }
  return { title: '设备生产贡献', subtitle: '有效长度与有效作业用时配对计算单位用时产出', rows: devices.value,
    option: weldingLengthOption(devices.value.map(row => `设备 ${row.device_no ?? '未记录'}`), devices.value.map(row => row.weld_length_m), colors.value.secondary),
    fields: [['device_no', '设备'], ['job_count', '作业次数'], ['weld_length_m', '焊接长度(m)'], ['working_hours', '作业用时(h)'], ['output_per_hour', '单位用时产出(m/h)']] }
})
const panels = computed(() => props.kind === 'parts' ? [first.value] : [first.value, second.value])
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
    <ReportTrendPanel v-for="(panel, index) in panels" :key="`${kind}-${index}`" :number="String(index + 1).padStart(2, '0')" :title="panel.title" :subtitle="panel.subtitle" :chart-label="panel.title" :height="280" :empty="!panel.rows.length" :has-data="panel.rows.length > 0">
      <template #chart><div v-if="kind === 'jobs' && index === 1 && !processes.some(row => row.length !== null && row.length > 0)" class="welding-chart-empty"><NEmpty description="暂无有效工艺长度数据" /></div><WeldingChart v-else :option="panel.option" :label="panel.title" /></template>
      <template #table><AppReportTable :columns="columns(panel.fields)" :items="panel.rows" :row-key="rowKey" :paginate="false" :max-height="280" /></template>
    </ReportTrendPanel>
  </div>
</template>

<style scoped>.welding-statistics{display:flex;flex-direction:column;gap:16px;min-width:0}.welding-statistics :deep(.report-trend-panel){margin-bottom:0}.welding-chart-empty{height:100%;display:flex;align-items:center;justify-content:center}</style>
