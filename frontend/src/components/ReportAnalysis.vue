<script setup lang="ts">
import type { AnalysisResult } from '../types/reports'
import { computed } from 'vue'
import { NAlert, NButton, NCollapse, NCollapseItem } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import { compactAnalysisFactLabel, compactAnalysisFactValue } from '../utils/analysisFactDisplay.ts'
import { formatDeviceDisplay } from '../utils/deviceDisplay.ts'

const props = withDefaults(defineProps<{
  analysis: AnalysisResult | null
  loading: boolean
  ready: boolean
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

const conclusionTitle = computed(() => sectionLead(props.analysis?.alerts || props.analysis?.overview, '生产运行分析已完成'))
const conclusionText = computed(() => plainAnalysisText(props.analysis?.overview || props.analysis?.features))
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
        <span v-if="analysis" class="analysis-mode model">
          AI 分析<small v-if="analysis.cached">已缓存</small>
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
      <section class="analysis-conclusion" aria-labelledby="analysis-conclusion-title">
        <span class="analysis-conclusion__icon"><AppIcon name="sparkles" :size="31" tone="current" /></span>
        <div class="analysis-conclusion__copy">
          <span>分析结论 <small>AI 生成</small></span>
          <h3 id="analysis-conclusion-title">{{ conclusionTitle }}</h3>
          <p>{{ conclusionText }}</p>
        </div>
        <blockquote>内容由 AI 生成。<br>具体结论请结合报表与现场记录核实。</blockquote>
      </section>

      <div class="analysis-section-heading"><div><AppIcon name="chart" :size="19" tone="current" /><h3>核心指标</h3></div><span>当前分析周期</span></div>
      <div v-if="analysis.facts?.length" class="analysis-facts">
        <article v-for="(fact, index) in analysis.facts" :key="fact.id">
          <span class="analysis-fact-icon"><AppIcon :name="['tasks','ruler','clock','chart','trend','database'][index % 6]" :size="19" tone="current" /></span>
          <div>
            <span :title="fact.label">{{ compactAnalysisFactLabel(fact.label, fact.value) }}</span>
            <strong :title="formatAnalysisText(fact.value)">{{ compactAnalysisFactValue(formatAnalysisText(fact.value)) }}</strong>
          </div>
        </article>
      </div>

      <div class="analysis-section-heading analysis-section-heading--results"><div><AppIcon name="sparkles" :size="19" tone="current" /><h3>分析结果</h3></div><span>可在下方查看模型输入数据</span></div>
      <div class="analysis-grid">
        <article v-for="section in [
          { key: 'overview', title: '生产概况', icon: 'chart' },
          { key: 'features', title: '变化情况', icon: 'trend' },
          { key: 'alerts', title: '需要关注', icon: 'alert' },
          { key: 'suggestions', title: '建议怎么做', icon: 'lightbulb' },
        ] as const" :key="section.key">
          <header><h3><AppIcon :name="section.icon" size="20" tone="current" />{{ section.title }}</h3></header>
          <div class="analysis-card-copy">
            <h4>{{ sectionLead(analysis[section.key], section.title) }}</h4>
            <p v-if="sectionBody(analysis[section.key])">{{ sectionBody(analysis[section.key]) }}</p>
          </div>
        </article>
      </div>
      <NCollapse v-if="analysis.facts?.length" class="analysis-evidence"><NCollapseItem name="evidence"><template #header><AppIcon name="database" :size="17" tone="current" />查看模型输入数据（{{ analysis.facts.length }} 项）</template>
        <div class="evidence-list">
          <article v-for="fact in analysis.facts" :key="`evidence-${fact.id}`">
            <strong>{{ fact.id }} · {{ fact.label }}</strong>
            <span>{{ formatAnalysisText(fact.value) }}</span>
            <small>来源：{{ fact.source || '当前报表数据' }}</small>
          </article>
        </div>
      </NCollapseItem></NCollapse>
      <p class="analysis-time">生成时间：{{ analysis.generated_at || '--' }}</p>
    </template>
    <div v-else-if="!loading && !error" class="analysis-state">{{ emptyMessage }}</div>
  </section>
</template>

<style scoped>
.analysis-panel{--analysis-navy:color-mix(in srgb,rgb(var(--primary-color)) 45%,var(--text-color-1));--analysis-blue:color-mix(in srgb,rgb(var(--info-color)) 45%,var(--text-color-1));--analysis-teal:var(--analysis-blue);--analysis-green:color-mix(in srgb,rgb(var(--success-color)) 45%,var(--text-color-1));--analysis-red:color-mix(in srgb,rgb(var(--error-color)) 45%,var(--text-color-1));--analysis-indigo:var(--analysis-navy);container:analysis-panel / inline-size;overflow:hidden;margin-bottom:0!important;padding:0;border:1px solid var(--theme-border-color)!important;border-radius:var(--report-panel-radius)!important;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow)!important}
.analysis-panel-head{min-height:66px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:16px!important;border-bottom:1px solid var(--theme-border-color);background:var(--panel-heading-bg)!important}
.analysis-panel-title{display:flex;align-items:center;gap:12px}.analysis-panel-icon{width:38px!important;height:38px!important;display:grid;place-items:center;flex:0 0 auto;color:var(--analysis-indigo);border-radius:50%;background:rgb(var(--primary-color) / .1)}
.analysis-panel-head h2{margin:2px 0 0!important;color:var(--text-color-1)!important;font-size:14px!important}.analysis-kicker{color:var(--analysis-blue);font-size:var(--font-auxiliary)!important;font-weight:800;letter-spacing:.12em}
.analysis-actions{display:flex;align-items:center;gap:8px;flex-shrink:0}.analysis-actions button,.analysis-error button{min-height:34px!important;padding:0 12px!important}
.analysis-mode{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;color:rgb(var(--info-color));border:1px solid var(--theme-border-color);border-radius:999px;background:rgb(var(--info-color) / .1);font-weight:700;white-space:normal;overflow-wrap:anywhere;max-width:100%}.analysis-mode.model{color:var(--analysis-green);border-color:var(--theme-border-color);background:rgb(var(--success-color) / .1)}.analysis-mode small{padding-left:8px;border-left:1px solid currentColor;font-size:var(--font-auxiliary);opacity:.72}
.analysis-reload{display:inline-flex;align-items:center;justify-content:center;gap:7px}
.analysis-state{padding:40px 24px;color:var(--text-color-2);text-align:center;background:rgb(var(--container-bg-color))}.analysis-error{display:flex;align-items:center;justify-content:center;gap:16px;margin:24px;padding:16px;color:rgb(var(--error-color));background:rgb(var(--error-color) / .1)}
.analysis-conclusion{display:grid;grid-template-columns:58px minmax(0,1fr) minmax(250px,.42fr);align-items:center;gap:16px;margin:16px;padding:16px;border:1px solid var(--theme-border-color);border-radius:var(--report-panel-radius);background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow)}
.analysis-conclusion__icon{width:50px;height:50px;display:grid;place-items:center;border-radius:50%;background:rgb(var(--primary-color));box-shadow:var(--theme-shadow)}.analysis-conclusion__copy{min-width:0}.analysis-conclusion__copy>span{color:var(--analysis-indigo);font-size:var(--font-auxiliary);font-weight:700}.analysis-conclusion__copy>span small{margin-left:8px;padding:4px 8px;color:var(--analysis-blue);border-radius:999px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary)}.analysis-conclusion h3{margin:4px 0 4px;color:var(--analysis-navy);font-size:14px;line-height:1.6}.analysis-conclusion p{margin:0;color:var(--text-color-2);font-size:var(--font-auxiliary);line-height:1.6}.analysis-conclusion blockquote{margin:0;padding:4px 0 4px 20px;color:var(--text-color-2);border-left:1px solid var(--theme-border-color);font-size:var(--font-auxiliary);line-height:1.6}
.analysis-section-heading{min-height:38px;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:0 16px;color:var(--text-color-1)}.analysis-section-heading>div{display:flex;align-items:center;gap:8px}.analysis-section-heading h3{margin:0;color:var(--text-color-1);font-size:14px}.analysis-section-heading>span{color:var(--text-color-2);font-size:var(--font-auxiliary)}.analysis-section-heading--results{margin-top:4px}
.analysis-facts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px!important;padding:0 16px 16px!important}.analysis-facts article{--fact-color:var(--analysis-blue);--fact-soft:rgb(var(--info-color) / .1);position:relative;min-width:0;min-height:104px;display:grid;grid-template-columns:29px minmax(0,1fr);align-items:center;gap:8px;overflow:visible;padding:12px!important;border:1px solid var(--theme-border-color);border-top:2px solid var(--fact-color);border-radius:var(--report-panel-radius);background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow)}
.analysis-facts article:nth-child(2){--fact-color:var(--analysis-teal);--fact-soft:rgb(var(--info-color) / .1)}.analysis-facts article:nth-child(3){--fact-color:var(--analysis-indigo);--fact-soft:rgb(var(--primary-color) / .1)}.analysis-facts article:nth-child(4){--fact-color:rgb(var(--primary-color));--fact-soft:rgb(var(--primary-color) / .1)}.analysis-facts article:nth-child(5){--fact-color:var(--text-color-2);--fact-soft:rgb(var(--info-color) / .1)}.analysis-facts article:nth-child(6){--fact-color:rgb(var(--primary-color));--fact-soft:rgb(var(--primary-color) / .1)}.analysis-facts article>div{min-width:0;display:contents}.analysis-fact-icon{width:28px;height:28px;display:grid!important;place-items:center;color:var(--fact-color)!important;border-radius:7px;background:var(--fact-soft)}.analysis-facts span:not(.analysis-fact-icon){grid-column:2;display:block;overflow:visible;color:var(--text-color-1);font-size:var(--font-auxiliary)!important;text-overflow:clip;white-space:normal;overflow-wrap:anywhere}.analysis-facts span b{float:right;margin:0;padding:2px 5px;color:var(--analysis-blue);border:1px solid var(--theme-border-color);border-radius:4px;background:rgb(var(--info-color) / .1);font-size:var(--font-auxiliary)}.analysis-facts strong{grid-column:1/-1;min-width:0;display:block;margin-top:0;overflow:visible;color:var(--text-color-1);font-size:18px!important;text-overflow:clip;white-space:normal;overflow-wrap:anywhere}
.analysis-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:12px!important;padding:0 16px 16px!important}.analysis-grid article{min-height:112px!important;padding:16px!important;border:1px solid var(--theme-border-color)!important;border-radius:var(--report-panel-radius)!important;background:rgb(var(--primary-color) / .1)!important}.analysis-grid article:nth-child(2){border-color:var(--theme-border-color)!important;background:rgb(var(--info-color) / .1)!important}.analysis-grid article:nth-child(3){border-color:var(--theme-border-color)!important;background:rgb(var(--error-color) / .1)!important}.analysis-grid article:nth-child(4){border-color:var(--theme-border-color)!important;background:rgb(var(--success-color) / .1)!important}.analysis-grid article>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.analysis-grid h3{display:flex;align-items:center;gap:7px;margin:0 0 8px;color:var(--analysis-blue);font-size:14px!important}.analysis-card-copy{min-width:0}.analysis-grid h4{display:block;margin:0 0 4px;overflow:visible;overflow-wrap:anywhere;color:var(--text-color-1);font-size:13px;line-height:1.6}.analysis-grid article:nth-child(2) h3{color:var(--analysis-teal)}.analysis-grid article:nth-child(3) h3{color:var(--analysis-red)}.analysis-grid article:nth-child(4) h3{color:var(--analysis-green)}.analysis-grid p{display:block;margin:0;overflow:visible;color:var(--text-color-2)!important;font-size:13px!important;line-height:1.6!important;overflow-wrap:anywhere;white-space:pre-wrap}.analysis-evidence{margin:0 16px 16px!important;border:1px solid var(--theme-border-color);border-radius:8px;background:rgb(var(--info-color) / .1)}.analysis-evidence summary{min-height:39px;display:flex;align-items:center;gap:8px;padding:0 16px;color:var(--text-color-1);cursor:pointer;font-size:var(--font-auxiliary);font-weight:700}.evidence-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;border-top:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color))}.evidence-list article{display:grid;gap:4px;padding:12px 16px;background:rgb(var(--container-bg-color))}.evidence-list article strong{color:var(--text-color-1);font-size:var(--font-auxiliary)}.evidence-list article span{color:var(--text-color-1);font-size:var(--font-auxiliary);font-weight:700}.evidence-list article small{color:var(--text-color-2);font-size:var(--font-auxiliary);line-height:1.5}.analysis-time{margin:0;padding:0 16px 16px!important;color:var(--text-color-2);text-align:right;font-size:var(--font-auxiliary)!important}
.analysis-facts article:last-child strong{overflow:visible;font-size:15px!important;line-height:1.3;white-space:normal;overflow-wrap:anywhere}
.analysis-conclusion__copy,.analysis-conclusion blockquote,.analysis-evidence .evidence-list article{min-width:0;overflow-wrap:anywhere}
@media(max-width:1180px){.analysis-facts{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:800px){.analysis-panel-head{align-items:flex-start;flex-direction:column;padding:16px}.analysis-actions{width:100%;justify-content:space-between}.analysis-conclusion{grid-template-columns:50px 1fr}.analysis-conclusion blockquote{grid-column:1/-1;padding:9px 0 0;border-top:1px solid var(--theme-border-color);border-left:0}.analysis-grid{grid-template-columns:1fr!important}.evidence-list{grid-template-columns:1fr}}
@media(max-width:720px){.analysis-actions{display:grid;grid-template-columns:1fr;gap:8px}.analysis-mode,.analysis-reload{width:100%;justify-content:center}.analysis-conclusion{margin:16px;padding:16px;gap:16px;border-radius:var(--report-panel-radius)}.analysis-conclusion__icon{width:44px;height:44px}.analysis-section-heading{padding:0 16px}.analysis-facts{grid-template-columns:repeat(2,minmax(0,1fr));padding:0 12px 9px!important}.analysis-grid{padding:0 16px 16px!important;gap:12px}.analysis-evidence{margin:0 16px 16px!important}}
@media(max-width:440px){.analysis-facts{grid-template-columns:1fr;gap:12px;padding:0 16px 16px}}
@container analysis-panel (max-width:900px){.analysis-facts{grid-template-columns:repeat(3,minmax(0,1fr))}}
@container analysis-panel (max-width:600px){.analysis-facts{grid-template-columns:repeat(2,minmax(0,1fr))}}
@container analysis-panel (max-width:360px){.analysis-facts{grid-template-columns:1fr}}
</style>
