<script setup>
import { computed } from 'vue'
import { NCard, NNumberAnimation, NSkeleton, NStatistic, NText } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import MetricSparkline from './MetricSparkline.vue'
import { useAppStore } from '../state/app.js'

const props = defineProps({
  title: { type: String, required: true },
  icon: { type: String, required: true },
  value: { type: [String, Number], default: null },
  precision: { type: Number, default: 0 },
  unit: { type: String, default: '' },
  status: { type: String, default: '' },
  direction: { type: String, default: 'flat' },
  series: { type: Array, default: () => [] },
  loading: Boolean,
})
const app = useAppStore()
const numericValue = computed(() => typeof props.value === 'number' && Number.isFinite(props.value) ? props.value : null)
</script>

<template>
  <NCard :bordered="false" size="small" class="card-wrapper min-h-146px bg-container transition-all-300" :aria-label="`${title}：${loading ? '正在加载' : `${value ?? '--'} ${unit}`}`">
    <NStatistic tabular-nums :value="numericValue === null ? value ?? '--' : app.reducedMotion ? numericValue : undefined" :precision="precision">
      <template #label>
        <div class="mb-8px flex-y-center gap-12px">
          <span class="h-36px w-36px flex-center shrink-0 rd-8px bg-primary/10 text-primary"><AppIcon :name="icon" :size="20" tone="current" /></span>
          <NText>{{ title }}</NText>
        </div>
      </template>
      <NSkeleton v-if="loading" height="30px" width="60%" aria-label="正在获取生产数据" role="status" />
      <NNumberAnimation v-else-if="numericValue !== null && !app.reducedMotion" :from="0" :to="numericValue" :precision="precision" show-separator />
      <template #suffix><NText v-if="unit && !loading" depth="3" class="text-12px">{{ unit }}</NText></template>
    </NStatistic>
    <div v-if="!loading" class="mt-12px flex-y-center justify-between gap-8px">
      <span class="text-12px" :class="direction === 'up' ? 'text-success' : direction === 'down' ? 'text-error' : 'text-gray-500 dark:text-gray-400'">{{ status }}</span>
      <MetricSparkline class="h-28px w-76px shrink-0 text-primary" :values="series" :label="`${title}趋势`" />
    </div>
  </NCard>
</template>
