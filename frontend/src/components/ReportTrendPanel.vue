<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import ReportPanelState from './ReportPanelState.vue'

const props = defineProps({
  number: { type: String, required: true },
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  note: { type: String, default: '' },
  chartLabel: { type: String, default: '' },
  showChart: { type: Boolean, default: true },
  fitTableContent: { type: Boolean, default: false },
  height: { type: Number, default: 300 },
  loading: Boolean,
  empty: Boolean,
  error: { type: String, default: '' },
  hasData: Boolean,
  loadingTitle: { type: String, default: '正在读取报表数据' },
  loadingMessage: { type: String, default: '数据返回后将自动更新图表和明细。' },
  emptyTitle: { type: String, default: '当前条件下暂无数据' },
  emptyMessage: { type: String, default: '可以调整统计区间或筛选条件后重新查询。' },
  errorTitle: { type: String, default: '等待报表数据' },
  retryLabel: { type: String, default: '重新查询' },
})

defineEmits<{ retry: [] }>()

const titleId = useId()
const chartElement = ref<HTMLElement | null>(null)
const state = computed<{ type: 'error' | 'loading' | 'empty'; title: string; message: string; actionLabel: string } | null>(() => {
  if (props.error && !props.hasData) return { type: 'error', title: props.errorTitle, message: props.error, actionLabel: props.retryLabel }
  if (props.loading && !props.hasData) return { type: 'loading', title: props.loadingTitle, message: props.loadingMessage, actionLabel: '' }
  if (props.empty && !props.hasData) return { type: 'empty', title: props.emptyTitle, message: props.emptyMessage, actionLabel: props.retryLabel }
  return null
})

defineExpose({ getChartElement: () => chartElement.value })
</script>

<template>
  <section class="panel report-trend-panel" :style="{ '--report-trend-height': `${height}px` }" :aria-labelledby="titleId">
    <header class="panel-title report-trend-panel__heading">
      <div><span>{{ number }}</span><h2 :id="titleId">{{ title }}</h2><small v-if="subtitle">{{ subtitle }}</small></div>
      <p v-if="loading && hasData" class="report-trend-panel__activity"><span class="spinner dark"></span>正在更新</p>
      <p v-else-if="note">{{ note }}</p>
    </header>
    <slot name="summary" />
    <div class="report-trend-panel__body">
      <div
        class="report-trend-panel__content"
        :class="{
          'report-trend-panel__content--table-only': !showChart,
          'report-trend-panel__content--fit-table': !showChart && fitTableContent,
          'is-obscured': state,
        }"
        :aria-hidden="state ? 'true' : undefined"
      >
        <div v-if="showChart" ref="chartElement" class="report-trend-panel__chart" role="img" :aria-label="chartLabel"></div>
        <div class="report-trend-panel__table"><slot name="table" /></div>
      </div>
      <ReportPanelState v-if="state" v-bind="state" class="report-trend-panel__state" @action="$emit('retry')" />
    </div>
    <slot name="footer" />
  </section>
</template>

<style scoped>
.report-trend-panel { --report-panel-padding:16px; margin-bottom:24px; overflow:hidden; container:report-panel / inline-size; }
.report-trend-panel__heading { gap:16px; flex-wrap:wrap; }
.report-trend-panel__heading > div { min-width:0; flex-wrap:wrap; row-gap:8px; padding-block:12px; }
.report-trend-panel__heading h2 { min-width:0; overflow-wrap:anywhere; line-height:1.4; }
.report-trend-panel__heading small { color:var(--muted); font-size:12px; }
.report-trend-panel__activity{display:flex;align-items:center;gap:7px;color:rgb(var(--info-color))!important;font-weight:700}.report-trend-panel__activity .spinner{width:13px;height:13px}
.report-trend-panel__body{display:grid;min-height:var(--report-trend-height)}
.report-trend-panel__content { grid-area:1/1; display:grid; grid-template-columns:minmax(330px,.9fr) minmax(0,1.25fr); gap:16px; align-items:stretch; padding:var(--report-panel-padding); }
.report-trend-panel__content.is-obscured{visibility:hidden;pointer-events:none}
.report-trend-panel__state{grid-area:1/1;z-index:2;margin:var(--report-panel-padding);border:1px solid var(--theme-border-color);border-radius:8px}
.report-trend-panel__content--table-only { grid-template-columns:minmax(0,1fr); }
.report-trend-panel__chart,.report-trend-panel__table { min-width:0; height:var(--report-trend-height); border:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.report-trend-panel__chart { width:100%; }
.report-trend-panel__table { max-height:var(--report-trend-height); overflow:auto; scrollbar-gutter:stable; }
.report-trend-panel__content--fit-table .report-trend-panel__table { height:auto; max-height:none; overflow-x:auto; overflow-y:visible; scrollbar-gutter:auto; }
@media (max-width:900px) { .report-trend-panel__content { grid-template-columns:1fr; gap:16px; }.report-trend-panel__table { height:auto; max-height:420px; } }
@media (max-width:560px) { .report-trend-panel { --report-panel-padding:12px; margin-bottom:24px; }.report-trend-panel__chart { height:260px; } }
@container report-panel (max-width:720px) {
  .report-trend-panel__body > .report-trend-panel__content { grid-template-columns:minmax(0,1fr); }
}
</style>
