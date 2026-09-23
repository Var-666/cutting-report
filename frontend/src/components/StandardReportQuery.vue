<script setup>
import { computed } from 'vue'
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
    type: Array,
    default: () => [
      { value: 'bar_line', label: '柱线组合' },
      { value: 'bar', label: '柱状图' },
      { value: 'line', label: '折线图' },
      { value: 'area', label: '面积图' },
    ],
  },
  loading: Boolean,
  hasData: Boolean,
  exportDisabled: Boolean,
  exporting: Boolean,
  ariaLabel: { type: String, default: '报表查询条件' },
})

const emit = defineEmits([
  'update:startDate',
  'update:endDate',
  'update:chartType',
  'submit',
  'export',
  'validation-error',
])

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
      <ReportPeriodPicker
        v-model:start-date="startDateModel"
        v-model:end-date="endDateModel"
        :anchor-date="anchorDate"
        :max-days="maxDays"
        :disabled="loading"
        @validation-error="emit('validation-error', $event)"
      />
      <label class="standard-report-query__chart">
        <span><AppIcon name="chart" size="13" />图表类型</span>
        <AppSelect v-model="chartTypeModel">
          <option v-for="option in chartOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </AppSelect>
      </label>
      <button class="primary standard-report-query__submit" type="submit" :disabled="loading">
        <span v-if="loading" class="spinner"></span>
        <AppIcon v-else name="search" size="15" tone="light" />
        {{ loading ? '查询中' : hasData ? '更新报表' : '查询报表' }}
      </button>
      <ReportExportMenu
        class="standard-report-query__export"
        :disabled="exportDisabled"
        :loading="exporting"
        @select="emit('export', $event)"
      />
    </form>
  </section>
</template>

<style scoped>
.standard-report-query { width:100%; display:grid; grid-template-columns:minmax(220px,1.3fr) minmax(110px,.55fr) 110px 118px; grid-template-areas:"period chart query export"; align-items:start; gap:10px; margin:0; padding:0; border:0; background:transparent; box-shadow:none; }
.standard-report-query :deep(.report-period-picker) { grid-area:period; display:grid; grid-template-rows:14px 40px; align-self:start; row-gap:6px; }
.standard-report-query__chart { grid-area:chart; min-width:0; display:grid; grid-template-rows:14px 40px; align-self:start; row-gap:6px; }
.standard-report-query__chart > span,
.standard-report-query :deep(.report-period-picker__label) { height:14px; display:flex; align-items:center; gap:5px; margin:0!important; color:#355a70!important; font-size:10px!important; font-weight:700!important; line-height:14px!important; letter-spacing:.12px!important; }
.standard-report-query__chart > .app-select { width:100%; height:40px; }
.standard-report-query :deep(.report-period-picker__trigger),
.standard-report-query__chart :deep(.app-select__trigger) { width:100%; height:40px!important; min-height:40px!important; border-color:#a9c0cc!important; border-radius:8px!important; background:#f7fafc!important; color:#183d54!important; font-size:11px!important; font-weight:600!important; }
.standard-report-query :deep(.report-period-picker__trigger:hover),
.standard-report-query__chart :deep(.app-select__trigger:hover) { border-color:#729cad!important; background:#edf5f8!important; }
.standard-report-query__submit { grid-area:query; width:100%; height:40px!important; min-height:40px!important; align-self:start; margin-top:20px; padding:0 12px; border-color:#0d789f!important; border-radius:8px!important; background:linear-gradient(135deg,#0d668d,#0796b7)!important; box-shadow:0 6px 14px rgba(12,117,156,.2)!important; font-size:11px!important; font-weight:700!important; }
.standard-report-query__submit:not(:disabled):hover { border-color:#138db4!important; background:linear-gradient(135deg,#0b7199,#0aa5c4)!important; box-shadow:0 8px 18px rgba(12,117,156,.25)!important; transform:translateY(-1px); }
.standard-report-query__submit .app-icon { filter:drop-shadow(0 1px 2px rgba(0,35,53,.18)); }
.standard-report-query__export { grid-area:export; align-self:start; margin-top:20px; }
.standard-report-query__export :deep(.app-select__trigger) { height:40px!important; min-height:40px!important; border-color:#a9c0cc!important; border-radius:8px!important; background:#f7fafc!important; color:#183d54!important; font-size:11px!important; font-weight:700!important; }
@media (max-width:900px) {
  .standard-report-query { grid-template-columns:minmax(0,1fr) 110px 118px; grid-template-areas:"period period period" "chart query export"; }
}
@media (max-width:700px) {
  .standard-report-query { grid-template-columns:minmax(0,1fr) minmax(0,1fr); grid-template-areas:"period period" "chart chart" "query export"; }
  .standard-report-query__submit,.standard-report-query__export { margin-top:0; }
}
@media (max-width:560px) {
  .standard-report-query { grid-template-columns:1fr; grid-template-areas:"period" "chart" "query" "export"; }
}
</style>
