<script setup>
import { ref, useId } from 'vue'

defineProps({
  number: { type: String, required: true },
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  note: { type: String, default: '' },
  chartLabel: { type: String, default: '' },
  showChart: { type: Boolean, default: true },
  fitTableContent: { type: Boolean, default: false },
  height: { type: Number, default: 300 },
})

const titleId = useId()
const chartElement = ref(null)

defineExpose({ getChartElement: () => chartElement.value })
</script>

<template>
  <section class="panel report-trend-panel" :style="{ '--report-trend-height': `${height}px` }" :aria-labelledby="titleId">
    <header class="panel-title report-trend-panel__heading">
      <div><span>{{ number }}</span><h2 :id="titleId">{{ title }}</h2><small v-if="subtitle">{{ subtitle }}</small></div>
      <p v-if="note">{{ note }}</p>
    </header>
    <slot name="summary" />
    <div
      class="report-trend-panel__content"
      :class="{
        'report-trend-panel__content--table-only': !showChart,
        'report-trend-panel__content--fit-table': !showChart && fitTableContent,
      }"
    >
      <div v-if="showChart" ref="chartElement" class="report-trend-panel__chart" role="img" :aria-label="chartLabel"></div>
      <div class="report-trend-panel__table"><slot name="table" /></div>
    </div>
    <slot name="footer" />
  </section>
</template>

<style scoped>
.report-trend-panel { margin-bottom:22px; overflow:hidden; }
.report-trend-panel__heading { gap:14px; flex-wrap:wrap; }
.report-trend-panel__heading small { color:#728595; font-size:12px; }
.report-trend-panel__content { display:grid; grid-template-columns:minmax(330px,.9fr) minmax(0,1.25fr); gap:14px; align-items:stretch; padding:16px 18px 20px; }
.report-trend-panel__content--table-only { grid-template-columns:minmax(0,1fr); }
.report-trend-panel__chart,.report-trend-panel__table { min-width:0; height:var(--report-trend-height); border:1px solid #e0e7ec; background:#fff; }
.report-trend-panel__chart { width:100%; }
.report-trend-panel__table { max-height:var(--report-trend-height); overflow:auto; scrollbar-gutter:stable; }
.report-trend-panel__content--fit-table .report-trend-panel__table { height:auto; max-height:none; }
@media (max-width:900px) { .report-trend-panel__content { grid-template-columns:1fr; }.report-trend-panel__table { height:auto; max-height:420px; } }
@media (max-width:560px) { .report-trend-panel__content { padding:12px; }.report-trend-panel__chart { height:260px; } }
</style>
