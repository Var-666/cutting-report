<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { deviceSelection, loadDevices } from '../state/devices.ts'
import { useProductionOverview } from '../composables/useProductionOverview.ts'
import { useDeviceSelection } from '../composables/useDeviceSelection.ts'
import { productionGroups, productionLineIcon } from '../utils/productionLines.ts'
import { overviewPeriod } from '../api/productionOverview.ts'
import AppIcon from '../components/AppIcon.vue'
import ProductionOverviewCard from '../components/ProductionOverviewCard.vue'
import ReportPanelState from '../components/ReportPanelState.vue'

const { month, rows, refresh, refreshingAll } = useProductionOverview(() => deviceSelection.items)
const { enterLine, switching } = useDeviceSelection()
const period = computed(() => overviewPeriod(month.value))
const groups = computed(() => productionGroups(rows.value.map(row => row.line))
  .map(group => ({ ...group, rows: rows.value.filter(row => row.line.type === group.id) })))
const currentLine = computed(() => deviceSelection.items.find(line => line.id === deviceSelection.id && line.enabled))
const groupFilter = ref('all')
const activeGroup = computed(() => groups.value.some(group => group.id === groupFilter.value) ? groupFilter.value : 'all')
const visibleGroups = computed(() => groups.value.filter(group => activeGroup.value === 'all' || group.id === activeGroup.value))
const canRefresh = computed(() => rows.value.some(row => row.line.enabled && !row.loading))
const overviewHeading = ref<HTMLElement | null>(null)
const shortcuts = [
  { route: 'pdm-statistics', icon: 'file', label: 'PDM 资料管理', description: '设备资料与详设图纸' },
  { route: 'settings', icon: 'settings', label: '系统设置', description: '数据源、监控与配置诊断' },
]

function focusOverview() {
  overviewHeading.value?.focus({ preventScroll: true })
  overviewHeading.value?.scrollIntoView({ block: 'start' })
}
</script>

<template>
  <div class="system-home">
    <div class="portal-top">
      <header class="portal-hero">
        <div class="portal-hero-copy">
          <h1>生产管理门户</h1>
          <p>查看切割与焊接产出，进入各产线的报表与管理页面。</p>
          <div class="portal-actions">
            <NButton v-if="currentLine" type="primary" class="portal-enter" :loading="switching" :disabled="switching"
              @click="enterLine(currentLine.id)">
              <span class="portal-enter-label" :title="currentLine.name">进入 {{ currentLine.name }}</span>
              <AppIcon name="chevron-right" :size="16" tone="current" />
            </NButton>
            <a class="portal-jump" href="#production-overview" @click.prevent="focusOverview">
              查看生产总览<AppIcon name="arrow-down" :size="14" tone="current" />
            </a>
          </div>
        </div>
      </header>
      <nav class="portal-shortcuts" aria-label="公共功能">
        <h2>公共功能</h2>
        <RouterLink v-for="item in shortcuts" :key="item.route" :to="{ name: item.route }" class="portal-shortcut">
          <span class="shortcut-icon"><AppIcon :name="item.icon" :size="20" tone="current" /></span>
          <span class="shortcut-copy"><strong>{{ item.label }}</strong><span>{{ item.description }}</span></span>
          <AppIcon name="chevron-right" :size="16" tone="current" class="shortcut-arrow" />
        </RouterLink>
      </nav>
    </div>
    <section class="production-overview" aria-labelledby="production-overview">
      <header class="page-heading overview-heading">
        <div class="overview-heading-copy">
          <h2 id="production-overview" ref="overviewHeading" tabindex="-1">生产总览</h2>
          <p class="overview-period">
            <AppIcon name="calendar" :size="16" tone="current" />
            <span>本月累计</span>
            <span class="overview-period-dates">{{ period.start }} 至 {{ period.end }}</span>
          </p>
        </div>
        <div class="page-heading__actions overview-actions">
          <div v-if="groups.length > 1" class="overview-filter" role="group" aria-label="按产线类型查看">
            <NButton size="small" :aria-pressed="activeGroup === 'all'" quaternary @click="groupFilter = 'all'">全部</NButton>
            <NButton v-for="group in groups" :key="group.id" size="small" :aria-pressed="activeGroup === group.id"
              quaternary @click="groupFilter = group.id">{{ group.name }}</NButton>
          </div>
          <NButton :loading="refreshingAll" :disabled="!canRefresh || refreshingAll" @click="refresh()">刷新总览</NButton>
        </div>
      </header>
      <ReportPanelState v-if="deviceSelection.error" type="error" title="产线目录读取失败" :message="rows.length ? '仍可使用上次读取的产线目录。' : '暂时无法获取产线，请重试。'" action-label="重试" @action="loadDevices({ force: true, includeLines: true })" />
      <ReportPanelState v-else-if="deviceSelection.loading || !deviceSelection.ready" type="loading" title="正在读取产线目录…" />
      <ReportPanelState v-else-if="!rows.length" title="暂无产线" message="当前未登记生产产线" />
      <div v-if="visibleGroups.length" class="overview-board">
        <section v-for="group in visibleGroups" :key="group.id" class="overview-group"
          :class="{ 'overview-group--welding': group.id === 'welding' }"
          :style="{ '--group-columns': Math.min(group.rows.length, 2) }" :aria-label="`${group.name}产线`">
          <header class="overview-group-heading">
            <h3><AppIcon :name="productionLineIcon(group.id)" :size="18" tone="current" />{{ group.name }}产线</h3>
            <span>{{ group.rows.length }} 条产线</span>
          </header>
          <div class="overview-group-cards">
            <ProductionOverviewCard v-for="row in group.rows" :key="row.line.id" :row="row" :switching="switching" @enter="enterLine(row.line.id)" @retry="refresh(row.line.id)" />
          </div>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.system-home { display:grid; gap:var(--space-6); min-width:0; container-type:inline-size; }
.portal-top { display:grid; grid-template-columns:minmax(0, 1fr) minmax(240px, 26%); gap:var(--space-4); }
.portal-hero {
  display:flex; align-items:center; min-height:248px; min-width:0;
  padding:24px clamp(24px, 3.2cqi, 40px); border-radius:var(--report-panel-radius);
  color:#fff; background:#111e2c;
  background-image:linear-gradient(90deg, #111e2c 0%, rgb(17 30 44 / .9) 30%, rgb(17 30 44 / .18) 72%, transparent), url('../assets/production-portal.webp');
  background-position:center, right 57%; background-size:cover; background-repeat:no-repeat;
  box-shadow:var(--theme-shadow);
}
.portal-hero-copy { min-width:0; max-width:min(480px, 70%); }
.portal-hero-copy::before { content:""; display:block; width:28px; height:3px; margin-block-end:16px; border-radius:2px; background:#ffad63; }
.portal-hero h1 { margin:0; font-size:clamp(28px, 4cqi, 44px); font-weight:700; line-height:1.18; }
.portal-hero p { margin:8px 0 24px; font-size:var(--font-body); line-height:1.65; color:#e0e9f3; }
.portal-actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.portal-enter { max-width:100%; min-height:var(--control-height); }
.portal-enter :deep(.n-button__content) { min-width:0; gap:8px; }
.portal-enter-label { min-width:0; overflow:hidden; text-overflow:ellipsis; }
.portal-jump {
  display:inline-flex; align-items:center; gap:var(--space-2); min-height:var(--control-height); padding-inline:var(--space-3);
  border:1px solid rgb(255 255 255 / .28); border-radius:var(--control-radius);
  background:rgb(17 30 44 / .48); color:inherit; text-decoration:none;
}
.portal-jump:hover { background:rgb(17 30 44 / .78); border-color:rgb(255 255 255 / .7); }
.portal-jump:focus-visible { outline:2px solid currentColor; outline-offset:3px; }
.portal-shortcuts {
  display:grid; grid-template-rows:auto repeat(2,minmax(0,1fr)); min-width:0; padding:16px;
  border:1px solid var(--theme-border-color); border-radius:var(--report-panel-radius); background:rgb(var(--container-bg-color));
  box-shadow:var(--theme-shadow);
}
.portal-shortcuts h2 { margin:0; padding:2px 8px 8px; font-size:var(--font-section-title); font-weight:650; }
.portal-shortcut {
  display:grid; grid-template-columns:40px minmax(0, 1fr) auto; align-items:center; gap:12px;
  min-width:0; padding:12px 8px; color:var(--text-color-1); text-decoration:none; border-radius:var(--card-radius);
}
.portal-shortcut + .portal-shortcut { border-block-start:1px solid var(--theme-divider-color); }
.portal-shortcut:hover { background:var(--theme-hover-color); color:var(--text-color-1); }
.portal-shortcut:hover strong { text-decoration:underline; }
.portal-shortcut:focus-visible { outline:2px solid rgb(var(--primary-color)); outline-offset:2px; }
.shortcut-icon { display:grid; place-items:center; height:40px; border-radius:var(--card-radius); background:rgb(var(--primary-color) / .1); color:rgb(var(--primary-color)); }
.portal-shortcut + .portal-shortcut .shortcut-icon { background:var(--theme-hover-color); color:var(--text-color-2); }
.shortcut-copy { display:grid; gap:4px; overflow-wrap:anywhere; }
.shortcut-copy strong { font-weight:600; }
.shortcut-copy span,.shortcut-arrow { color:var(--text-color-3); }
.shortcut-copy span { font-size:var(--font-auxiliary); }
.production-overview { display:grid; gap:var(--space-4); min-width:0; }
.overview-heading-copy { display:grid; gap:4px; }
.overview-heading h2 { margin:0; font-size:24px; font-weight:700; scroll-margin-block-start:16px; }
.overview-heading h2:focus-visible { outline:2px solid rgb(var(--primary-color)); outline-offset:4px; }
.overview-period { display:flex; align-items:center; flex-wrap:wrap; gap:4px 8px; margin:0; font-size:var(--font-auxiliary); color:var(--text-color-2); }
.overview-period-dates { color:var(--text-color-3); font-variant-numeric:tabular-nums; }
.overview-filter { display:flex; flex-wrap:wrap; align-items:center; gap:4px; padding:4px; border:1px solid var(--theme-border-color); border-radius:var(--card-radius); background:rgb(var(--container-bg-color)); }
.overview-filter :deep(.n-button) { min-width:48px; }
.overview-filter :deep(.n-button[aria-pressed="true"]) { color:var(--text-color-1); background:rgb(var(--primary-color) / .14); font-weight:600; }
.overview-board { display:flex; flex-wrap:wrap; align-items:flex-start; gap:12px; }
.overview-group {
  --line-accent:var(--primary-color);
  display:grid; gap:12px; min-width:0; container-type:inline-size;
  flex:var(--group-columns) 1 min(100%, calc(var(--group-columns) * 312px));
  max-inline-size:calc(var(--group-columns) * 440px + (var(--group-columns) - 1) * 12px);
}
.overview-group--welding { --line-accent:var(--success-color); }
.overview-group-cards { display:grid; grid-template-columns:repeat(var(--group-columns), minmax(0,1fr)); gap:12px; }
.overview-group-heading { display:flex; align-items:center; gap:12px; min-width:0; padding-inline:4px; }
.overview-group-heading::after { content:""; flex:1; order:1; border-block-start:1px solid var(--theme-border-color); }
.overview-group-heading h3 { display:flex; align-items:center; gap:8px; margin:0; font-size:var(--font-section-title); font-weight:600; }
.overview-group-heading .app-icon { color:rgb(var(--line-accent)); }
.overview-group-heading > span { order:2; font-size:var(--font-auxiliary); color:var(--text-color-3); }
@container (max-width:580px) {
  .overview-group-cards { grid-template-columns:minmax(0,1fr); }
}
@container (max-width:760px) {
  .portal-top { grid-template-columns:minmax(0,1fr); }
  .portal-hero { min-height:208px; }
  .portal-shortcuts { grid-template-rows:auto; grid-template-columns:repeat(2,minmax(0,1fr)); }
  .portal-shortcuts h2 { grid-column:1 / -1; }
  .portal-shortcut + .portal-shortcut { border-block-start:0; border-inline-start:1px solid var(--theme-divider-color); }
}
@container (max-width:460px) {
  .portal-hero {
    padding:16px; background-position:center, 63% center;
    background-image:linear-gradient(90deg, rgb(17 30 44 / .98), rgb(17 30 44 / .76) 67%, rgb(17 30 44 / .36)), url('../assets/production-portal.webp');
  }
  .portal-hero-copy { max-width:100%; }
  .portal-hero-copy::before { margin-block-end:12px; }
  .portal-hero p { margin-block-end:16px; }
  .portal-shortcuts { padding:8px; }
  .portal-shortcuts h2 { padding-block-end:4px; }
  .portal-shortcut { grid-template-columns:28px minmax(0,1fr); gap:8px; align-content:start; }
  .shortcut-icon { height:28px; }
  .shortcut-icon .app-icon { width:16px; height:16px; }
  .shortcut-arrow { display:none; }
  .overview-actions { width:100%; justify-content:space-between; }
}
</style>
