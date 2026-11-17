<script setup lang="ts">
import { computed } from 'vue'
import { useThemeVars } from 'naive-ui'
import ReportChart from '../WeldingChart.vue'
import ReportPanelState from '../ReportPanelState.vue'
import type { PdmSummaryTable } from '../../api/generated/types.gen'
import { pdmCompletionChartOption } from '../../utils/pdmReport'

const props = defineProps<{ table: PdmSummaryTable; detail?: boolean }>()
const theme = useThemeVars()
const option = computed(() => pdmCompletionChartOption(props.table, {
  onTime: theme.value.successColor, total: theme.value.infoColor, text: theme.value.textColor2,
  border: theme.value.borderColor, divider: theme.value.dividerColor,
}, props.detail))
</script>

<template>
  <ReportChart v-if="table.totals.total > 0" :option="option" :label="`${table.title} · ${table.scope_label}：按时完成率与总完成率柱状图`" />
  <ReportPanelState v-else class="pdm-completion-chart__empty" type="empty" title="当前条件下暂无可统计文档" message="可通过本表的筛选按钮调整条件。" />
</template>

<style scoped>
.pdm-completion-chart__empty{height:100%}
</style>
