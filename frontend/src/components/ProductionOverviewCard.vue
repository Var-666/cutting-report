<script setup lang="ts">
import type { OverviewRow } from '../composables/useProductionOverview.ts'
import { computed } from 'vue'
import { NAlert, NButton, NTag } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import HomeKpiCard from './HomeKpiCard.vue'
import ReportPanelState from './ReportPanelState.vue'
import { productionLineIcon } from '../utils/productionLines.ts'

const props = defineProps<{ row: OverviewRow; switching?: boolean }>()
defineEmits<{ enter: []; retry: [] }>()

const result = computed(() => props.row.data?.preparing ? null : props.row.data)
const status = computed(() => {
  const { row } = props
  if (row.loading) return { text: '正在更新', type: 'info' } as const
  if (result.value?.refreshing && result.value.refreshFailed && !row.error) return { text: '正在重试', type: 'warning' } as const
  if (row.error || result.value?.refreshFailed) return { text: '刷新失败', type: 'warning' } as const
  if (result.value?.refreshing) return { text: '后台更新中', type: 'info' } as const
  if (result.value?.stale) return { text: '历史结果待更新', type: 'warning' } as const
  if (result.value?.empty) return { text: '本月暂无生产记录', type: 'default' } as const
  return null
})
</script>

<template>
  <article class="overview-card" :aria-label="row.line.name">
    <header class="overview-line">
      <span class="overview-line-icon">
        <AppIcon :name="productionLineIcon(row.line.type)" :size="20" tone="current" />
      </span>
      <h4 class="overview-card-title">{{ row.line.name }}</h4>
      <NButton class="overview-enter" size="small" quaternary
        :disabled="switching" :aria-label="`进入产线：${row.line.name}`" @click="$emit('enter')">
        进入产线<AppIcon name="chevron-right" :size="14" tone="current" />
      </NButton>
    </header>
    <div class="overview-card-body">
      <div v-if="row.line.enabled && result" class="overview-metrics">
        <HomeKpiCard v-for="(metric, index) in result.metrics" :key="metric.title"
          :class="{ 'overview-output': index === 1 }" :title="metric.title" :value="metric.value"
          :unit="metric.unit" :precision="metric.precision" color="rgb(var(--overview-accent))"
          :icon="['tasks', 'ruler', 'clock'][index]" trend-label="" />
      </div>
      <p v-else-if="!row.line.enabled" class="overview-line-state" role="status">
        <NTag size="small" :bordered="false">产线未启用</NTag>
      </p>
      <NAlert v-else-if="row.error" type="error" role="alert" title="生产数据读取失败">
        <div class="overview-error"><span>当前无可用结果</span><NButton size="small" :aria-label="`重试读取：${row.line.name}`" @click="$emit('retry')">重试</NButton></div>
      </NAlert>
      <ReportPanelState v-else-if="row.loading || row.preparing || row.data?.preparing" class="overview-line-state" type="loading"
        :title="row.preparing || row.data?.preparing ? '生产数据准备中…' : '正在读取本月生产数据…'" />
      <p v-else class="overview-line-state" role="status">尚未读取生产数据</p>
    </div>
    <footer v-if="row.line.enabled && result && (result.demo || status)" class="overview-details" role="status">
      <NTag v-if="result.demo" size="small" type="warning" :bordered="false">演示数据</NTag>
      <NTag v-if="status" size="small" :type="status.type" :bordered="false">{{ status.text }}</NTag>
      <NButton v-if="!row.loading && (row.error || result.refreshFailed)" size="tiny" :aria-label="`重试读取：${row.line.name}`" @click="$emit('retry')">重试</NButton>
    </footer>
  </article>
</template>

<style scoped>
.overview-card {
  --overview-accent:var(--line-accent, var(--primary-color));
  display:flex; flex-direction:column; min-width:0;
  border:1px solid var(--theme-border-color);
  border-radius:var(--card-radius); background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow);
}
.overview-card:focus-within { border-color:rgb(var(--overview-accent) / .55); }
.overview-line {
  display:grid; grid-template-columns:36px minmax(0,1fr) auto;
  align-items:center; gap:8px; min-height:68px; padding:12px 16px;
}
.overview-line-icon {
  display:grid; place-items:center; width:36px; height:36px;
  border-radius:var(--card-radius); background:rgb(var(--overview-accent) / .11); color:rgb(var(--overview-accent));
}
.overview-card-title { min-width:0; margin:0; overflow-wrap:anywhere; font-size:var(--font-section-title); font-weight:650; line-height:1.4; }
.overview-enter { justify-self:end; background:rgb(var(--overview-accent) / .08); }
.overview-enter .app-icon { margin-inline-start:4px; }
.overview-card-body { flex:1; display:grid; align-content:start; gap:12px; min-width:0; padding:0 16px 16px; }
.overview-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px 0; }
.overview-metrics :deep(.home-kpi) { min-height:0; padding:0 8px; gap:4px; }
.overview-metrics :deep(.home-kpi:not(.overview-output)) { --font-home-kpi-value:var(--font-kpi-value); justify-content:start; }
.overview-metrics :deep(.home-kpi:last-child) { padding-inline-start:12px; border-inline-start:1px solid var(--theme-divider-color); }
.overview-metrics :deep(.overview-output) {
  position:relative; isolation:isolate; grid-area:1 / 1 / 2 / -1; padding:12px;
  border:1px solid rgb(var(--overview-accent) / .12); border-radius:var(--card-radius);
  background:linear-gradient(100deg, rgb(var(--overview-accent) / .1), rgb(var(--overview-accent) / .035));
}
.overview-metrics :deep(.overview-output)::before {
  content:""; position:absolute; z-index:-1; inset:0 0 0 60%; pointer-events:none;
  border-radius:inherit;
  background:repeating-linear-gradient(135deg, transparent 0 9px, rgb(var(--overview-accent) / .16) 9px 10px);
  mask-image:linear-gradient(90deg, transparent, black);
}
.overview-metrics :deep(.n-statistic-value) { display:flex; align-items:baseline; flex-wrap:wrap; line-height:1.24; }
.overview-metrics :deep(.n-statistic-value__content) { min-width:0; overflow-wrap:anywhere; }
.overview-metrics :deep(.home-kpi__trend) { display:none; }
.overview-line-state { margin:0; padding:0; }
.overview-details { padding:8px 16px; border-top:1px solid var(--theme-divider-color); }
.overview-details,.overview-error { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-1); }
.overview-error { justify-content:space-between; gap:8px; overflow-wrap:anywhere; }
</style>
