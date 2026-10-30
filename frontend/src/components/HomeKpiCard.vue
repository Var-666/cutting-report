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
})
const app = useAppStore()
const numericValue = computed(() => typeof props.value === 'number' && Number.isFinite(props.value) ? props.value : null)
const formattedValue = computed(() => numericValue.value === null ? props.value ?? '--' : numericValue.value.toLocaleString('zh-CN', { minimumFractionDigits: props.precision, maximumFractionDigits: props.precision }))
</script>

<template>
  <div class="min-w-0 min-h-176px flex-col-stretch justify-between gap-panel p-panel" :style="{ '--kpi-color': color }" :aria-label="`${title}：${loading ? '正在加载' : `${formattedValue} ${unit}`}`">
    <div class="flex-y-center flex-wrap justify-between gap-field">
      <div class="flex-y-center gap-field"><AppIcon v-if="icon" :name="icon" :size="18" tone="current" :style="{ color }" /><NBadge dot v-else :color="color" :processing="!loading && numericValue !== null && !app.reducedMotion" aria-hidden="true" /><NText depth="2" class="text-auxiliary">{{ title }}</NText></div>
      <NTag v-if="!loading && status" size="small" :bordered="false" :type="direction === 'up' ? 'success' : direction === 'down' ? 'error' : 'default'" :aria-label="status">{{ status.replace(' 较上月', '') }}</NTag>
    </div>
    <NStatistic tabular-nums :value="loading || (numericValue !== null && !app.reducedMotion) ? undefined : formattedValue" :theme-overrides="{ valueFontSize: 'var(--font-home-kpi-value)', valueFontWeight: '600' }">
      <NSkeleton v-if="loading" height="36px" width="60%" aria-label="正在获取生产数据" role="status" />
      <NNumberAnimation v-else-if="numericValue !== null && !app.reducedMotion" :from="0" :to="numericValue" :precision="precision" show-separator />
      <template #suffix><NText v-if="unit && !loading" depth="2" class="text-auxiliary">{{ unit }}</NText></template>
    </NStatistic>
    <div class="flex-y-center justify-between gap-field">
      <NText depth="2" class="text-auxiliary">{{ trendLabel }}</NText>
      <NSkeleton v-if="loading" height="28px" width="76px" />
      <MetricSparkline v-else-if="series.length" class="h-28px w-76px shrink-0" variant="bar" :values="series" :label="`${title}趋势`" />
    </div>
  </div>
</template>
