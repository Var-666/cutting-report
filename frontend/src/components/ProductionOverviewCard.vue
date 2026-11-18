<script setup lang="ts">
import type { OverviewRow } from '../composables/useProductionOverview.ts'
import { NAlert, NButton, NTag } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import HomeKpiCard from './HomeKpiCard.vue'
import ReportPanelState from './ReportPanelState.vue'
import { formatChineseDateTime } from '../utils/dateDisplay.ts'
import { productionLineIcon } from '../utils/productionLines.ts'

defineProps<{ row: OverviewRow }>()
defineEmits<{ enter: []; retry: [] }>()
</script>

<template>
  <article class="overview-card" :aria-label="row.line.name">
    <header class="overview-line">
      <span class="overview-line-icon">
        <AppIcon :name="productionLineIcon(row.line.type)" :size="20" tone="current" />
      </span>
      <h4 class="overview-card-title">{{ row.line.name }}</h4>
      <NButton class="overview-enter" size="small" quaternary
        :aria-label="`进入产线：${row.line.name}`" @click="$emit('enter')">
        进入产线<AppIcon name="chevron-right" :size="14" tone="current" />
      </NButton>
    </header>
    <div class="overview-card-body">
      <div v-if="row.line.enabled && row.data" class="overview-metrics">
        <HomeKpiCard v-for="(metric, index) in row.data.metrics" :key="metric.title"
          :class="{ 'overview-output': index === 1 }" :title="metric.title" :value="metric.value"
          :unit="metric.unit" :precision="metric.precision" color="rgb(var(--overview-accent))"
          :icon="['tasks', 'ruler', 'clock'][index]" trend-label="" />
      </div>
      <p v-else-if="!row.line.enabled" class="overview-line-state" role="status">
        <NTag size="small" :bordered="false">产线未启用</NTag>
      </p>
      <ReportPanelState v-else-if="row.loading" class="overview-line-state" type="loading"
        :title="row.preparing ? '生产数据准备中…' : '正在读取本月生产数据…'" />
      <NAlert v-if="row.error" type="error" role="alert" :title="row.data ? '刷新失败，保留上次结果' : '生产数据读取失败'">
        <div class="overview-error"><span>{{ row.error }}</span><NButton size="small" :disabled="row.loading" @click="$emit('retry')">重试</NButton></div>
      </NAlert>
    </div>
    <footer v-if="row.line.enabled && row.data" class="overview-details">
      <div class="overview-card-meta" role="status">
        <div class="overview-statuses">
          <NTag v-if="row.data.preparing" size="small" type="info" :bordered="false">生产数据准备中</NTag>
          <NTag v-if="row.data.demo" size="small" type="warning" :bordered="false">演示数据</NTag>
          <NTag v-if="row.data.stale" size="small" type="warning" :bordered="false">数据待更新</NTag>
          <NTag v-if="row.data.refreshFailed" size="small" type="warning" :bordered="false">后台刷新失败</NTag>
          <NTag v-else-if="row.data.refreshing" size="small" type="info" :bordered="false">后台更新中</NTag>
          <NTag v-if="row.data.empty" size="small" :bordered="false">本月暂无生产记录</NTag>
          <span v-if="row.loading">{{ row.preparing ? '生产数据准备中…' : '正在更新…' }}</span>
          <NButton v-if="row.data.refreshFailed" size="tiny" :disabled="row.loading" @click="$emit('retry')">重新读取</NButton>
        </div>
        <span class="overview-timestamp">
          <AppIcon name="clock" :size="14" tone="current" />
          更新于 <time v-if="row.data.dataAsOf" :datetime="row.data.dataAsOf">{{ formatChineseDateTime(row.data.dataAsOf) }}</time><span v-else>未提供</span>
        </span>
        <span>{{ row.data.timezone ? `统计时区：${row.data.timezone}` : '按切割报表生产日期统计' }}</span>
      </div>
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
.overview-card-meta { display:flex; flex-wrap:wrap; gap:2px 8px; align-items:center; text-align:start; font-size:var(--font-auxiliary); color:var(--text-color-3); }
.overview-timestamp,.overview-statuses,.overview-error { display:flex; align-items:center; flex-wrap:wrap; gap:4px; }
.overview-timestamp time { font-variant-numeric:tabular-nums; }
.overview-statuses { flex-basis:100%; padding-bottom:4px; }
.overview-statuses:empty { display:none; }
.overview-error { justify-content:space-between; gap:8px; overflow-wrap:anywhere; }
</style>
