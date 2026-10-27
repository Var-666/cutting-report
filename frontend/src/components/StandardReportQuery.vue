<script setup lang="ts">
import type { ExportFormat } from '../types/api'

import type { PropType } from 'vue'

import { computed } from 'vue'
import { NButton } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import ReportExportMenu from './ReportExportMenu.vue'
import ReportPeriodPicker from './ReportPeriodPicker.vue'

const props = defineProps({
  startDate: { type: String, required: true },
  endDate: { type: String, required: true },
  anchorDate: { type: String, default: '' },
  maxDays: { type: Number, default: 366 },
  chartType: { type: String, required: true },
  chartOptions: {
    type: Array as PropType<{ value: string; label: string }[]>,
    default: () => [
      { value: 'bar_line', label: '柱线组合' },
      { value: 'bar', label: '柱状图' },
      { value: 'line', label: '折线图' },
      { value: 'area', label: '面积图' },
    ],
  },
  loading: Boolean,
  hasData: Boolean,
  dateDirty: Boolean,
  exportDisabled: Boolean,
  exporting: Boolean,
  ariaLabel: { type: String, default: '报表查询条件' },
})

const emit = defineEmits<{ 'update:startDate': [date: string]; 'update:endDate': [date: string]; 'update:chartType': [chart: string]; submit: []; export: [format: ExportFormat]; 'validation-error': [message: string] }>()

const startDateModel = computed({
  get: () => props.startDate,
  set: (value) => emit('update:startDate', value),
})
const endDateModel = computed({
  get: () => props.endDate,
  set: (value) => emit('update:endDate', value),
})
const chartTypeModel = computed({
  get: () => props.chartType,
  set: (value) => emit('update:chartType', value),
})
</script>

<template>
  <section class="standard-report-query-shell" :aria-label="ariaLabel">
    <form class="standard-report-query" :aria-busy="loading" @submit.prevent="emit('submit')">
      <div class="standard-report-query__period">
        <ReportPeriodPicker
          v-model:start-date="startDateModel"
          v-model:end-date="endDateModel"
          :anchor-date="anchorDate"
          :max-days="maxDays"
          :disabled="loading"
          @validation-error="emit('validation-error', $event)"
        />
        <p v-if="dateDirty && !loading" class="standard-report-query__pending" role="status" aria-live="polite">条件已修改，尚未更新报表</p>
      </div>
      <label class="standard-report-query__chart">
        <span><AppIcon name="chart" size="13" tone="current" />图表类型</span>
        <AppSelect v-model="chartTypeModel">
          <option v-for="option in chartOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </AppSelect>
      </label>
      <NButton class="standard-report-query__submit" type="primary" attr-type="submit" :disabled="loading">
        <span v-if="loading" class="spinner"></span>
        <AppIcon v-else name="search" size="15" tone="current" />
        {{ loading ? '查询中' : hasData ? '更新报表' : '查询报表' }}
      </NButton>
      <ReportExportMenu
        class="standard-report-query__export"
        :disabled="exportDisabled"
        :loading="exporting"
        @select="emit('export', $event as ExportFormat)"
      />
    </form>
  </section>
</template>

<style scoped>
.standard-report-query-shell { min-width:0; container-type:inline-size; }
.standard-report-query { width:100%; display:grid; grid-template-columns:minmax(220px,1.3fr) minmax(110px,.55fr) 110px 132px; grid-template-areas:"period chart query export"; align-items:start; gap:8px; margin:0; padding:0; border:0; background:transparent; box-shadow:none; }
.standard-report-query__period { grid-area:period; min-width:0; }
.standard-report-query :deep(.report-period-picker) { display:grid; grid-template-rows:16px 40px; align-self:start; row-gap:8px; }
.standard-report-query__pending { min-height:16px; display:flex; align-items:center; gap:6px; margin:5px 0 0; color:rgb(var(--warning-color)); font-size:var(--font-auxiliary); font-weight:600; line-height:16px; }
.standard-report-query__pending::before { width:6px; height:6px; flex:0 0 auto; border-radius:50%; background:rgb(var(--warning-color)); box-shadow:var(--theme-shadow); content:""; }
.standard-report-query__chart { grid-area:chart; min-width:0; display:grid; grid-template-rows:16px 40px; align-self:start; row-gap:8px; }
.standard-report-query__chart > span,
.standard-report-query :deep(.report-period-picker__label) { height:16px; display:flex; align-items:center; gap:4px; margin:0!important; color:rgb(var(--info-color))!important; font-size:var(--font-auxiliary)!important; font-weight:700!important; line-height:16px!important; letter-spacing:.12px!important; }
.standard-report-query__chart > .app-select { width:100%; height:40px; }
.standard-report-query :deep(.n-base-selection) { --n-height:40px!important; }
.standard-report-query :deep(.report-period-picker__trigger),
.standard-report-query__chart :deep(.n-base-selection) { width:100%; height:40px!important; min-height:40px!important; border-color:var(--theme-border-color)!important; border-radius:8px!important; background:rgb(var(--info-color) / .1)!important; color:var(--text-color-1)!important; font-size:var(--font-auxiliary)!important; font-weight:600!important; }
.standard-report-query :deep(.report-period-picker__trigger:hover),
.standard-report-query__chart :deep(.n-base-selection:hover) { border-color:var(--theme-border-color)!important; background:rgb(var(--info-color) / .1)!important; }
.standard-report-query__submit { grid-area:query; width:100%; height:40px!important; min-height:40px!important; align-self:start; margin-top:24px; padding:0 12px; border-radius:8px!important; font-size:var(--font-auxiliary)!important; }
.standard-report-query__export { grid-area:export; align-self:start; margin-top:24px; }
.standard-report-query__export :deep(.n-base-selection) { height:40px!important; min-height:40px!important; border-radius:8px!important; font-size:var(--font-auxiliary)!important; font-weight:700!important; }
@media (max-width:900px) {
  .standard-report-query { grid-template-columns:minmax(0,1fr) 110px 132px; grid-template-areas:"period period period" "chart query export"; }
}
@media (max-width:700px) {
  .standard-report-query { grid-template-columns:minmax(0,1fr) minmax(0,1fr); grid-template-areas:"period period" "chart chart" "query export"; }
  .standard-report-query__submit,.standard-report-query__export { margin-top:0; }
}
@media (max-width:560px) {
  .standard-report-query { grid-template-columns:1fr; grid-template-areas:"period" "chart" "query" "export"; gap:8px; }
}
@container (max-width:560px) {
  .standard-report-query { grid-template-columns:minmax(0,1fr) minmax(0,1fr); grid-template-areas:"period period" "chart chart" "query export"; }
  .standard-report-query__submit,.standard-report-query__export { margin-top:0; }
}
@container (max-width:360px) {
  .standard-report-query { grid-template-columns:1fr; grid-template-areas:"period" "chart" "query" "export"; }
}
</style>
