<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { analyzeReport } from '../api/reports'

const props = defineProps({
  reportType: { type: String, required: true },
  reportDate: { type: String, default: '' },
  periodStart: { type: String, default: '' },
  periodEnd: { type: String, default: '' },
  ready: { type: Boolean, default: false },
  dataVersion: { type: String, default: '' },
})

const AUTO_ANALYSIS_DEBOUNCE_MS = 4000
const analysis = ref(null)
const loading = ref(false)
const pending = ref(false)
const error = ref('')
let requestSequence = 0
let debounceTimer = null
let activeRequestKey = ''
let displayedRequestKey = ''

const modeLabel = computed(() => {
  if (analysis.value?.mode === 'model') return '大模型分析'
  if (analysis.value?.mode === 'simulated_fallback') return '真实模型不可用 · 模拟分析'
  return '模拟大模型分析'
})

function formatAnalysisText(value) {
  return String(value ?? '').replace(/-?\d[\d,]*\.\d{2,}/g, (raw) => {
    const numeric = Number(raw.replaceAll(',', ''))
    if (!Number.isFinite(numeric)) return raw
    return new Intl.NumberFormat('zh-CN', {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
      useGrouping: raw.includes(','),
    }).format(numeric)
  })
}

function analysisParts(value) {
  const text = formatAnalysisText(value)
  const parts = []
  const citationPattern = /\[(F\d{3})\]/g
  let cursor = 0
  let match
  while ((match = citationPattern.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push({ type: 'text', value: text.slice(cursor, match.index) })
    }
    parts.push({ type: 'citation', value: match[1] })
    cursor = citationPattern.lastIndex
  }
  if (cursor < text.length) parts.push({ type: 'text', value: text.slice(cursor) })
  return parts
}

function requestKey() {
  return JSON.stringify([
    props.reportType,
    props.reportDate,
    props.periodStart,
    props.periodEnd,
    props.dataVersion,
  ])
}

function clearScheduledAnalysis() {
  if (debounceTimer !== null) window.clearTimeout(debounceTimer)
  debounceTimer = null
  pending.value = false
}

async function loadAnalysis(force = false, targetKey = requestKey()) {
  if (!props.ready || !props.reportDate) return
  const sequence = ++requestSequence
  activeRequestKey = targetKey
  pending.value = false
  loading.value = true
  error.value = ''
  try {
    const result = await analyzeReport({
      reportType: props.reportType,
      reportDate: props.reportDate,
      periodStart: props.periodStart || undefined,
      periodEnd: props.periodEnd || undefined,
      force,
    })
    if (sequence === requestSequence) {
      analysis.value = result
      displayedRequestKey = targetKey
    }
  } catch (exception) {
    if (sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      activeRequestKey = ''
    }
  }
}

function scheduleAutomaticAnalysis() {
  clearScheduledAnalysis()
  if (!props.ready || !props.reportDate) {
    requestSequence += 1
    activeRequestKey = ''
    loading.value = false
    analysis.value = null
    displayedRequestKey = ''
    error.value = ''
    return
  }
  const targetKey = requestKey()
  if (targetKey === displayedRequestKey || (loading.value && targetKey === activeRequestKey)) return

  // This source version only schedules the request. The analysis API rebuilds the
  // authoritative data_version and checks SQLite before it can call the model.
  // Invalidate a response for data that changed while the request was running.
  requestSequence += 1
  activeRequestKey = ''
  loading.value = false
  pending.value = true
  debounceTimer = window.setTimeout(() => {
    debounceTimer = null
    loadAnalysis(false, targetKey)
  }, AUTO_ANALYSIS_DEBOUNCE_MS)
}

function reloadAnalysis() {
  clearScheduledAnalysis()
  return loadAnalysis(true)
}

function retryAnalysis() {
  clearScheduledAnalysis()
  return loadAnalysis(false)
}

watch(
  () => [props.reportType, props.reportDate, props.periodStart, props.periodEnd, props.ready, props.dataVersion],
  scheduleAutomaticAnalysis,
  { immediate: true },
)

onBeforeUnmount(() => {
  clearScheduledAnalysis()
  requestSequence += 1
})
</script>

<template>
  <section class="panel analysis-panel" aria-live="polite">
    <div class="analysis-panel-head">
      <div>
        <span class="analysis-kicker">AI REPORT ANALYSIS</span>
        <h2>智能分析</h2>
      </div>
      <div class="analysis-actions">
        <span v-if="analysis" class="analysis-mode" :class="analysis.mode">
          {{ modeLabel }}<small v-if="analysis.cached">已缓存</small>
        </span>
        <span v-if="pending" class="analysis-pending">数据更新待分析</span>
        <button type="button" :disabled="loading || !ready" @click="reloadAnalysis">
          <span v-if="loading" class="spinner dark"></span>{{ loading ? '分析中' : '重新分析' }}
        </button>
      </div>
    </div>

    <div v-if="pending && !analysis" class="analysis-state">报表数据已加载，稳定 4 秒后自动分析…</div>
    <div v-else-if="loading && !analysis" class="analysis-state">正在整理报表指标并生成分析…</div>
    <div v-else-if="error" class="analysis-error">
      <span>{{ error }}</span><button type="button" @click="retryAnalysis">重试</button>
    </div>
    <template v-else-if="analysis">
      <div v-if="analysis.facts?.length" class="analysis-facts">
        <article v-for="fact in analysis.facts" :key="fact.id">
          <span><b>{{ fact.id }}</b>{{ fact.label }}</span><strong>{{ formatAnalysisText(fact.value) }}</strong>
        </article>
      </div>
      <div class="analysis-grid">
        <article v-for="section in [
          { key: 'overview', title: '生产概况' },
          { key: 'features', title: '数据特征' },
          { key: 'alerts', title: '异常与关注点' },
          { key: 'suggestions', title: '生产管理建议' },
        ]" :key="section.key">
          <h3>{{ section.title }}</h3>
          <p><template v-for="(part, index) in analysisParts(analysis[section.key])" :key="`${section.key}-${index}`"><sup v-if="part.type === 'citation'" class="analysis-citation">{{ part.value }}</sup><span v-else>{{ part.value }}</span></template></p>
        </article>
      </div>
      <details v-if="analysis.facts?.length" class="analysis-evidence">
        <summary>查看可追溯数据依据（{{ analysis.facts.length }} 项）</summary>
        <div class="evidence-list">
          <article v-for="fact in analysis.facts" :key="`evidence-${fact.id}`">
            <strong>{{ fact.id }} · {{ fact.label }}</strong>
            <span>{{ formatAnalysisText(fact.value) }}</span>
            <small>来源：{{ fact.source || '当前报表数据' }}</small>
          </article>
        </div>
      </details>
      <p class="analysis-time">生成时间：{{ analysis.generated_at || '--' }}</p>
    </template>
    <div v-else class="analysis-state">报表数据加载后将自动生成分析。</div>
  </section>
</template>

<style scoped>
.analysis-panel { padding:0; overflow:hidden; }
.analysis-panel-head { display:flex; align-items:center; justify-content:space-between; gap:24px; padding:24px 28px; border-bottom:1px solid #dce5ec; }
.analysis-panel-head h2 { margin:3px 0 4px; color:#172234; font-size:25px; }
.analysis-panel-head p { margin:0; color:#66788a; }
.analysis-kicker { color:#23849a; font-size:12px; font-weight:800; letter-spacing:.12em; }
.analysis-actions { display:flex; align-items:center; gap:12px; flex-shrink:0; }
.analysis-actions button,.analysis-error button { min-height:40px; padding:0 16px; }
.analysis-mode { display:inline-flex; align-items:center; gap:8px; padding:7px 11px; color:#875700; background:#fff4d6; border:1px solid #f0d58a; border-radius:999px; font-weight:700; white-space:nowrap; }
.analysis-mode.model { color:#087443; background:#e9f8f0; border-color:#9cd8b9; }
.analysis-mode.simulated_fallback { color:#a43b22; background:#fff0ec; border-color:#efb9aa; }
.analysis-mode small { padding-left:8px; border-left:1px solid currentColor; font-size:11px; opacity:.72; }
.analysis-pending { color:#875700; font-size:12px; white-space:nowrap; }
.analysis-facts { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; padding:20px 28px 0; }
.analysis-facts article { padding:12px 14px; background:#f4f8fa; border:1px solid #dbe6eb; border-radius:7px; }
.analysis-facts span { display:block; color:#687b8d; font-size:12px; }
.analysis-facts span b { margin-right:6px; color:#23849a; font-size:10px; }
.analysis-facts strong { display:block; margin-top:3px; color:#0e5277; font-size:16px; }
.analysis-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding:20px 28px; }
.analysis-grid article { min-height:126px; padding:17px 18px; border:1px solid #dce5ec; border-radius:8px; background:#fff; }
.analysis-grid h3 { margin:0 0 8px; color:#14597e; font-size:16px; }
.analysis-grid p { margin:0; color:#34485b; line-height:1.75; white-space:pre-wrap; }
.analysis-citation { display:inline-block; margin:0 2px; padding:1px 4px; color:#1b7188; border:1px solid #b8dbe3; border-radius:4px; background:#eef8fa; font-size:9px; font-weight:800; line-height:1.2; vertical-align:super; white-space:nowrap; }
.analysis-evidence { margin:0 28px 18px; border:1px solid #dce5ec; border-radius:8px; background:#fbfcfd; }
.analysis-evidence summary { padding:13px 15px; color:#14597e; cursor:pointer; font-weight:700; }
.evidence-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:1px; border-top:1px solid #dce5ec; background:#dce5ec; }
.evidence-list article { display:grid; gap:4px; padding:13px 15px; background:#fff; }
.evidence-list article strong { color:#263d50; font-size:13px; }
.evidence-list article span { color:#0e5277; font-weight:700; }
.evidence-list article small { color:#718393; line-height:1.5; }
.analysis-time { margin:0; padding:0 28px 20px; color:#7b8c9b; text-align:right; font-size:12px; }
.analysis-state { padding:42px 28px; color:#6d7f90; text-align:center; }
.analysis-error { display:flex; align-items:center; justify-content:center; gap:14px; margin:22px 28px; padding:16px; color:#a83a2b; background:#fff2ef; }
@media (max-width:800px) {
  .analysis-panel-head { align-items:flex-start; flex-direction:column; }
  .analysis-actions { width:100%; justify-content:space-between; }
  .analysis-grid { grid-template-columns:1fr; }
  .evidence-list { grid-template-columns:1fr; }
}
</style>
