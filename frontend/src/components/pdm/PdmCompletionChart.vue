<script setup lang="ts">
import { computed } from 'vue'
import { useThemeVars } from 'naive-ui'
import WeldingChart from '../WeldingChart.vue'
import ReportPanelState from '../ReportPanelState.vue'
import type { PdmSummaryTable } from '../../api/generated/types.gen'
import { pdmChartRows, pdmCompletionChartOption, pdmCompletionChartWidth } from '../../utils/pdmReport'

const props = defineProps<{ table: PdmSummaryTable; detail?: boolean; opensDetails?: boolean }>()
const emit = defineEmits<{ openDetails: [] }>()
const theme = useThemeVars()
const rows = computed(() => pdmChartRows(props.table, props.detail))
const hasData = computed(() => rows.value.some(row => row.total > 0))
const chartWidth = computed(() => pdmCompletionChartWidth(rows.value.length))
const option = computed(() => pdmCompletionChartOption(props.table, {
  onTime: theme.value.successColor, total: theme.value.infoColor, text: theme.value.textColor2,
  border: theme.value.borderColor, divider: theme.value.dividerColor,
}, props.detail))
const chartLabel = computed(() => `${props.table.title} · ${props.table.scope_label}：按时完成率与总完成率柱状图`)
function openDetails(event?: KeyboardEvent) {
  if (!props.opensDetails) return
  event?.preventDefault()
  emit('openDetails')
}
</script>

<template>
  <div class="pdm-completion-chart__scroll" :class="{ 'pdm-completion-chart__open': opensDetails }"
    :role="opensDetails ? 'button' : 'region'" :aria-haspopup="opensDetails ? 'dialog' : undefined"
    :aria-label="opensDetails ? `${chartLabel}，查看完整明细` : `${table.title}完成率图表滚动区域`" tabindex="0"
    @click="openDetails()" @keydown.enter.self="openDetails($event)" @keydown.space.self="openDetails($event)">
    <WeldingChart v-if="hasData" :option="option" :style="{ minWidth: `${chartWidth}px` }" :label="chartLabel" />
    <ReportPanelState v-else class="pdm-completion-chart__empty" type="empty" title="当前条件下暂无可统计文档" message="可通过本表的筛选按钮调整条件。" />
  </div>
</template>

<style scoped>
.pdm-completion-chart__scroll{overflow-x:auto}
.pdm-completion-chart__open{cursor:pointer}
.pdm-completion-chart__scroll:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:-2px}
.pdm-completion-chart__scroll :deep(.welding-chart){height:270px}
.pdm-completion-chart__empty{min-height:180px}
</style>
