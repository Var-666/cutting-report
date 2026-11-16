<script setup lang="ts">
import type { PropType } from 'vue'
import { computed } from 'vue'
import { NBadge, NNumberAnimation, NSkeleton, NStatistic, NTag, NText } from 'naive-ui'
import MetricSparkline from './MetricSparkline.vue'
import AppIcon from './AppIcon.vue'
import { useAppStore } from '../state/app.ts'

const props = defineProps({
  title: { type: String, required: true },
  color: { type: String, default: 'rgb(var(--primary-color))' },
  value: { type: [String, Number] as PropType<string | number | null>, default: null },
  precision: { type: Number, default: 0 },
  unit: { type: String, default: '' },
  status: { type: String, default: '' },
  direction: { type: String, default: 'flat' },
  series: { type: Array, default: () => [] },
  loading: Boolean,
  icon: { type: String, default: '' },
  trendLabel: { type: String, default: '近期每日趋势' },
  variant: { type: String as PropType<'home' | 'report'>, default: 'home' },
})
const app = useAppStore()
const numericValue = computed(() => typeof props.value === 'number' && Number.isFinite(props.value) ? props.value : null)
const formattedValue = computed(() => numericValue.value === null ? props.value ?? '--' : numericValue.value.toLocaleString('zh-CN', { minimumFractionDigits: props.precision, maximumFractionDigits: props.precision }))
</script>

<template>
  <div class="home-kpi min-w-0 min-h-176px flex-col-stretch justify-between gap-panel p-panel" :class="{ 'home-kpi--report': variant === 'report' }" :style="{ '--kpi-color': color }" :aria-label="`${title}：${loading ? '正在加载' : `${formattedValue} ${unit}`}`">
    <div class="flex-y-center flex-wrap justify-between gap-field">
      <div class="home-kpi__title flex-y-center gap-field"><span v-if="icon" class="home-kpi__icon"><AppIcon :name="icon" :size="variant === 'report' ? 22 : 18" tone="current" :style="{ color }" /></span><NBadge v-else dot :color="color" :processing="!loading && numericValue !== null && !app.reducedMotion" aria-hidden="true" /><NText depth="2" class="text-auxiliary">{{ title }}</NText></div>
      <NTag v-if="!loading && status" size="small" :bordered="false" :type="direction === 'up' ? 'success' : direction === 'down' ? 'error' : 'default'" :aria-label="status">{{ status.replace(' 较上月', '') }}</NTag>
    </div>
    <NStatistic tabular-nums :value="loading || (numericValue !== null && !app.reducedMotion) ? undefined : formattedValue" :theme-overrides="{ valueFontSize: 'var(--font-home-kpi-value)', valueFontWeight: '600' }">
      <NSkeleton v-if="loading" height="36px" width="60%" aria-label="正在获取生产数据" role="status" />
      <NNumberAnimation v-else-if="numericValue !== null && !app.reducedMotion" :from="0" :to="numericValue" :precision="precision" show-separator />
      <template #suffix><NText v-if="unit && !loading" depth="2" class="text-auxiliary">{{ unit }}</NText></template>
    </NStatistic>
    <div class="home-kpi__trend flex-y-center justify-between gap-field">
      <NText depth="2" class="text-auxiliary">{{ trendLabel }}</NText>
      <NSkeleton v-if="loading" height="28px" width="76px" />
      <MetricSparkline v-else-if="series.length" class="h-28px w-76px shrink-0" :variant="variant === 'report' ? 'line' : 'bar'" :values="series" :label="`${title}趋势`" />
    </div>
  </div>
</template>

<style scoped>
.home-kpi__icon { display:inline-flex; flex-shrink:0; }
.home-kpi--report { --font-home-kpi-value:var(--font-kpi-value); display:grid; grid-template-columns:minmax(0,1fr); grid-template-rows:minmax(35px,auto) auto 1fr; min-height:168px; padding:12px; gap:8px; border:1px solid var(--theme-border-color); border-top:2px solid var(--kpi-color); border-radius:var(--report-panel-radius); background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.home-kpi--report .home-kpi__title { min-width:0; gap:12px; }
.home-kpi--report .home-kpi__title :deep(.n-text) { color:var(--text-color-1); font-weight:700; }
.home-kpi--report .home-kpi__icon { width:35px; height:35px; display:grid; place-items:center; border-radius:50%; background:color-mix(in srgb,var(--kpi-color) 10%,transparent); }
.home-kpi--report .home-kpi__trend { flex-wrap:wrap; align-content:end; align-items:end; }
.home-kpi--report .home-kpi__trend :deep(.data-sparkline) { width:58px; height:25px; margin-left:auto; opacity:.78; }
</style>
