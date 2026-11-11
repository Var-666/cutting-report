<script setup lang="ts">
import type { AnalysisResult, LeadershipReport } from '../types/reports'
import type { WeldingReport } from '../types/welding'
import { computed } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import CuttingAnalysisBrief from './CuttingAnalysisBrief.vue'
import MetricSparkline from './MetricSparkline.vue'
import { compactAnalysisFactLabel, compactAnalysisFactValue } from '../utils/analysisFactDisplay.ts'
import { formatDeviceDisplay } from '../utils/deviceDisplay.ts'
import { weldingKpis, weldingMetricSeries } from '../utils/weldingReport.ts'

const props = withDefaults(defineProps<{
  analysis: AnalysisResult | null
  loading: boolean
  ready: boolean
  leadershipReport?: LeadershipReport | null
  weldingReport?: WeldingReport | null
  readingLatest?: boolean
  error?: string
  actionLabel?: string
  emptyMessage?: string
}>(), { readingLatest: false, error: '', actionLabel: '重新分析', emptyMessage: '报表数据加载后将自动生成分析。' })
const emit = defineEmits<{ reload: []; retry: [] }>()

function formatAnalysisText(value: unknown) {
  return formatDeviceDisplay(value).replace(/-?\d[\d,]*\.\d{2,}/g, (raw) => {
    const numeric = Number(raw.replaceAll(',', ''))
    if (!Number.isFinite(numeric)) return raw
    return new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
      useGrouping: raw.includes(','),
    }).format(numeric)
  })
}

function plainAnalysisText(value: unknown) {
  return formatAnalysisText(value).replace(/\[(F\d{3})\]/g, '').replace(/\s+/g, ' ').trim()
}

function sectionLead(value: unknown, fallback = '数据分析结论') {
  const text = plainAnalysisText(value)
  if (!text) return fallback
  const sentence = text.split(/[。；;！!？?]/)[0].trim()
  return sentence
}

function sectionBody(value: unknown) {
  const text = plainAnalysisText(value)
  const firstBreak = text.search(/[。；;！!？?]/)
  if (firstBreak < 0 || firstBreak >= text.length - 1) return ''
  return text.slice(firstBreak + 1).trim()
}

const analysisAvailable = computed(() => props.analysis?.mode === 'model' && props.analysis.status === 'success')
const conclusionTitle = computed(() => analysisAvailable.value
  ? sectionLead(props.analysis?.overview, '生产运行分析已完成') : '智能分析暂不可用')
const conclusionText = computed(() => analysisAvailable.value
  ? sectionBody(props.analysis?.overview) : plainAnalysisText(props.analysis?.overview || props.analysis?.features))
const welding = computed(() => props.weldingReport && props.analysis?.data_version === props.weldingReport.data_version
  ? props.weldingReport : null)
const weldingMetrics = computed(() => welding.value ? weldingKpis('daily', welding.value).map((metric, index) => ({
  ...metric,
  icon: ['tasks', 'ruler', 'grid', 'clock'][index],
  values: weldingMetricSeries(welding.value, metric.key),
  display: typeof metric.value === 'number' ? metric.value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : metric.value ?? '—',
})) : [])
</script>

<template>
  <section class="panel analysis-panel" aria-live="polite">
    <div class="analysis-panel-head">
      <div class="analysis-panel-title">
        <span class="analysis-panel-icon"><AppIcon name="sparkles" size="21" tone="current" /></span>
        <div>
          <span class="analysis-kicker">AI REPORT ANALYSIS</span>
          <h2>智能分析</h2>
        </div>
      </div>
      <div class="analysis-actions">
        <span v-if="analysis" class="analysis-mode" :class="{ model: analysisAvailable, unavailable: !analysisAvailable }">
          {{ analysisAvailable ? 'AI 分析' : '智能分析暂不可用' }}<small v-if="analysis.cached">已缓存</small>
        </span>
        <NButton attr-type="button" class="analysis-reload" :disabled="loading || !ready" @click="emit('reload')" :loading="loading">
          <AppIcon v-if="!loading" name="activity" :size="14" tone="current" />
          {{ loading ? (readingLatest ? '读取中' : '分析中') : actionLabel }}
        </NButton>
      </div>
    </div>

    <div v-if="loading && !analysis" class="analysis-state">{{ readingLatest ? '正在读取最近一次已完成的分析…' : '正在整理报表指标并生成分析…' }}</div>
    <NAlert v-else-if="error" type="error" class="analysis-error" role="alert">
      <span>{{ error }}</span><NButton attr-type="button" @click="emit('retry')">重试</NButton>
    </NAlert>
    <template v-if="analysis">
      <CuttingAnalysisBrief v-if="analysis.brief && analysisAvailable" :brief="analysis.brief" :facts="analysis.facts" :generated-at="analysis.generated_at" :leadership-report="leadershipReport" />
      <template v-else>
      <section class="analysis-conclusion" :class="{ 'analysis-conclusion--report': analysisAvailable }" aria-labelledby="analysis-conclusion-title">
        <span class="analysis-conclusion__icon"><AppIcon name="sparkles" :size="31" tone="light" /></span>
        <div class="analysis-conclusion__copy">
          <span>分析结论 <small v-if="analysisAvailable">AI 生成</small></span>
          <p v-if="welding" class="analysis-scope">{{ welding.query.start }} 至 {{ welding.query.end }}</p>
          <h3 id="analysis-conclusion-title">{{ conclusionTitle }}</h3>
          <p>{{ conclusionText }}</p>
        </div>
      </section>

      <template v-if="analysisAvailable">
      <div v-if="weldingMetrics.length" class="analysis-facts analysis-facts--welding" aria-label="关键指标">
        <article v-for="metric in weldingMetrics" :key="metric.key">
          <span class="analysis-fact-icon"><AppIcon :name="metric.icon" :size="21" tone="current" /></span>
          <div><span>{{ metric.title }}</span><strong>{{ metric.display }}<small v-if="typeof metric.value === 'number'"> {{ metric.unit }}</small></strong></div>
          <MetricSparkline v-if="metric.values.filter(value => value !== null).length > 1" class="analysis-sparkline"
            :values="metric.values" :label="`${metric.title}每日趋势`" :variant="metric.key === 'job_count' ? 'bar' : 'line'" />
        </article>
      </div>
      <div v-else-if="analysis.facts?.length" class="analysis-facts" aria-label="关键指标">
        <article v-for="(fact, index) in analysis.facts" :key="fact.id ?? undefined">
          <span class="analysis-fact-icon"><AppIcon :name="['tasks','ruler','clock','chart','trend','database'][index % 6]" :size="19" tone="current" /></span>
          <div>
            <span :title="fact.label">{{ compactAnalysisFactLabel(fact.label, fact.value) }}</span>
            <strong :title="formatAnalysisText(fact.value)">{{ compactAnalysisFactValue(formatAnalysisText(fact.value)) }}</strong>
          </div>
        </article>
      </div>

      <div v-if="analysis.features || analysis.alerts" class="analysis-section-heading"><div><h3>关键发现</h3></div></div>
      <div class="analysis-grid">
        <article v-for="section in [
          { key: 'features', title: '变化情况', icon: 'trend' },
          { key: 'alerts', title: '需要关注', icon: 'alert' },
        ].filter(section => section.key === 'features' ? analysis?.features : analysis?.alerts)" :key="section.key"
          :class="{ 'analysis-grid-alert': section.key === 'alerts' }">
          <header><h3><AppIcon :name="section.icon" size="20" tone="current" />{{ section.title }}</h3></header>
          <div class="analysis-card-copy">
            <h4>{{ sectionLead(section.key === 'features' ? analysis.features : analysis.alerts, section.title) }}</h4>
            <p>{{ sectionBody(section.key === 'features' ? analysis.features : analysis.alerts) }}</p>
          </div>
        </article>
      </div>
      <template v-if="analysis.suggestions">
        <div class="analysis-section-heading"><div><h3>建议跟进</h3></div></div>
        <div class="analysis-grid analysis-followup">
          <article><h3><AppIcon name="check" size="20" tone="current" />{{ sectionLead(analysis.suggestions) }}</h3><p>{{ sectionBody(analysis.suggestions) }}</p></article>
        </div>
      </template>
      </template>
      <NCollapse v-if="analysis.facts?.length || welding?.meta.notices.length" class="analysis-evidence"><NCollapseItem name="evidence"><template #header><AppIcon name="database" :size="17" tone="current" />分析依据<span v-if="analysis.facts?.length"> · 查看模型输入数据（{{ analysis.facts.length }} 项）</span></template>
        <ul v-if="welding?.meta.notices.length" class="analysis-notices"><li v-for="notice in welding.meta.notices" :key="notice">{{ notice }}</li></ul>
        <div class="evidence-list">
          <article v-for="fact in analysis.facts" :key="`evidence-${fact.id}`">
            <strong>{{ fact.id }} · {{ fact.label }}</strong>
            <span>{{ formatAnalysisText(fact.value) }}</span>
            <small>来源：{{ fact.source || '当前报表数据' }}</small>
          </article>
        </div>
      </NCollapseItem></NCollapse>
      </template>
      <p class="analysis-time">生成时间：{{ analysis.generated_at || '--' }}</p>
    </template>
    <div v-else-if="!loading && !error" class="analysis-state">{{ emptyMessage }}</div>
  </section>
</template>

<style scoped>
.analysis-panel {
  --analysis-blue:color-mix(in srgb,var(--analysis-accent) 80%,var(--text-color-1));
  --analysis-green:color-mix(in srgb,rgb(var(--success-color)) 45%,var(--text-color-1));
  container:analysis-panel / inline-size; overflow:hidden; margin-bottom:0; padding:0;
  border:1px solid var(--analysis-border); border-radius:var(--report-panel-radius);
  background:var(--analysis-panel-bg); box-shadow:var(--analysis-shadow);
}
.analysis-panel-head{min-height:76px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:16px;border-bottom:1px solid var(--analysis-border)}
.analysis-panel-title{display:flex;align-items:center;gap:12px}
.analysis-panel-icon{width:38px;height:38px;display:grid;place-items:center;flex:none;color:var(--analysis-blue);border-radius:10px;background:color-mix(in srgb,var(--analysis-accent) 12%,var(--analysis-surface))}
.analysis-panel-head h2{margin:2px 0 0!important;color:var(--text-color-1)!important;font-size:14px!important}.analysis-kicker{color:var(--analysis-blue);font-size:var(--font-auxiliary)!important;font-weight:800;letter-spacing:.12em}
.analysis-actions{display:flex;align-items:center;gap:8px;flex-shrink:0}.analysis-actions button,.analysis-error button{min-height:34px!important;padding:0 12px!important}
.analysis-mode{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;color:var(--analysis-blue);border:1px solid var(--theme-border-color);border-radius:999px;background:rgb(var(--info-color) / .1);font-weight:700;white-space:normal;overflow-wrap:anywhere;max-width:100%}.analysis-mode.model{color:var(--analysis-green);border-color:color-mix(in srgb,rgb(var(--success-color)) 26%,var(--analysis-surface));background:color-mix(in srgb,rgb(var(--success-color)) 9%,var(--analysis-surface))}.analysis-mode small{padding-left:8px;border-left:1px solid currentColor;font-size:var(--font-auxiliary)}
.analysis-mode.unavailable{color:rgb(var(--warning-color));background:rgb(var(--warning-color) / .1)}
.analysis-reload :deep(.n-button__content) { gap:6px; }
.analysis-state{padding:40px 24px;color:var(--text-color-2);text-align:center;background:rgb(var(--container-bg-color))}.analysis-error{display:flex;align-items:center;justify-content:center;gap:16px;margin:24px;padding:16px;color:rgb(var(--error-color));background:rgb(var(--error-color) / .1)}
.analysis-conclusion {
  position:relative; isolation:isolate; display:flex; align-items:flex-start; gap:12px;
  margin:12px; padding:14px 16px; overflow:hidden; border:1px solid var(--theme-border-color);
  border-radius:var(--report-panel-radius); background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow);
}
.analysis-conclusion--report {
  border-color:color-mix(in srgb,var(--analysis-accent) 22%,var(--analysis-surface));
  background:linear-gradient(105deg,var(--analysis-hero-start),var(--analysis-hero-end));
  box-shadow:none;
}
.analysis-conclusion--report::before {
  content:''; position:absolute; z-index:-1; inset:0 0 0 auto; width:34%;
  background:linear-gradient(95deg,var(--analysis-hero-end),transparent 46%),
    url('../assets/home-laser-cutting.webp') 78% 57% / auto 165% no-repeat;
}
.analysis-conclusion__icon,.analysis-fact-icon {
  display:grid; place-items:center; flex:none; width:38px; height:38px; border-radius:9px;
  color:var(--analysis-blue); background:color-mix(in srgb,var(--analysis-accent) 11%,var(--analysis-surface));
}
.analysis-conclusion__copy { min-width:0; max-width:62%; overflow-wrap:anywhere; }
.analysis-conclusion__copy > span,.analysis-scope { color:var(--text-color-2); font-size:var(--font-auxiliary); }
.analysis-conclusion__copy > span small { margin-left:6px; color:var(--analysis-blue); font-size:inherit; }
.analysis-conclusion h3 { margin:4px 0; font-size:20px; line-height:1.4; }
.analysis-conclusion p { margin:0; color:var(--text-color-2); line-height:1.65; }
.analysis-section-heading { margin:12px 12px 8px; padding-left:8px; border-left:4px solid var(--analysis-accent); }
.analysis-section-heading h3 { margin:0; font-size:var(--font-section-title); }
.analysis-facts,.analysis-grid { display:grid; gap:12px; padding:0 12px 12px; }
.analysis-facts { grid-template-columns:repeat(3,minmax(0,1fr)); }
.analysis-facts--welding { grid-template-columns:repeat(4,minmax(0,1fr)); }
.analysis-facts article,.analysis-grid article,.analysis-evidence {
  min-width:0; padding:12px; border:1px solid var(--theme-border-color);
  border-radius:var(--report-panel-radius); background:var(--analysis-surface); box-shadow:var(--analysis-shadow);
}
.analysis-facts article { display:grid; grid-template-columns:38px minmax(0,1fr); align-items:center; gap:8px; }
.analysis-facts article > div { min-width:0; overflow-wrap:anywhere; }
.analysis-facts article > div > span { font-size:var(--font-auxiliary); color:var(--text-color-2); }
.analysis-facts strong { display:block; font-size:var(--font-kpi-value); line-height:1.2; font-variant-numeric:tabular-nums; }
.analysis-facts small { font-size:var(--font-auxiliary); }
.analysis-sparkline { grid-column:1/-1; width:68px; height:24px; justify-self:end; --kpi-color:var(--analysis-accent); }
.analysis-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
.analysis-grid article { border-top:3px solid var(--analysis-accent); box-shadow:none; }
.analysis-grid article:only-child,.analysis-followup article { grid-column:1/-1; }
.analysis-grid .analysis-grid-alert { border-top-color:var(--analysis-warning-accent); }
.analysis-followup article { border-top:1px solid var(--theme-border-color); border-left:3px solid var(--analysis-accent); }
.analysis-grid h3 { display:flex; align-items:center; gap:7px; margin:0 0 6px; font-size:14px; }
.analysis-grid h3 .app-icon { color:var(--analysis-blue); flex:none; }
.analysis-grid h4 { margin:0 0 4px; font-size:var(--font-body); overflow-wrap:anywhere; }
.analysis-grid p { margin:0; color:var(--text-color-2); line-height:1.65; overflow-wrap:anywhere; white-space:pre-wrap; }
.analysis-grid p:empty { display:none; }
.analysis-evidence { margin:0 12px 12px; }
.analysis-notices { padding-left:20px; line-height:1.65; }
.evidence-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 16px; }
.evidence-list article { min-width:0; display:grid; gap:4px; overflow-wrap:anywhere; font-size:var(--font-auxiliary); }
.evidence-list article small { color:var(--text-color-2); }
.analysis-time{margin:0;padding:0 16px 16px!important;color:var(--text-color-2);text-align:right;font-size:var(--font-auxiliary)!important}
@media(max-width:720px) {
  .analysis-panel-head { align-items:flex-start; flex-direction:column; gap:12px; }
  .analysis-actions { width:100%; justify-content:space-between; flex-wrap:wrap; }
}
@container analysis-panel (max-width:850px) {
  .analysis-facts { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .analysis-conclusion--report::before { opacity:.24; }
  .analysis-conclusion__copy { max-width:100%; }
}
@container analysis-panel (max-width:540px) {
  .analysis-conclusion--report::before { opacity:.2; width:48%; }
  .analysis-conclusion__copy { max-width:100%; }
  .analysis-conclusion h3 { font-size:var(--font-section-title); }
  .analysis-facts article { grid-template-columns:30px minmax(0,1fr); padding:8px; }
  .analysis-fact-icon { width:30px; height:30px; }
  .analysis-facts strong { font-size:18px; }
  .analysis-grid,.evidence-list { grid-template-columns:1fr; }
}
</style>
