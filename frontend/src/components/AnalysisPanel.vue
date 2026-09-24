<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { analyzeReport } from '../api/reports'
import { createActivationTimer } from '../utils/activationTimer.js'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  reportType: { type: String, required: true },
  reportDate: { type: String, default: '' },
  periodStart: { type: String, default: '' },
  periodEnd: { type: String, default: '' },
  ready: { type: Boolean, default: false },
  dataVersion: { type: String, default: '' },
})

const analysis = ref(null)
const loading = ref(false)
const error = ref('')
const expandedSections = ref(new Set())
let requestSequence = 0
let activeRequestKey = ''
let displayedRequestKey = ''
let requestController = null
const activationTimer = createActivationTimer({
  setTimer: (callback, delay) => window.setTimeout(callback, delay),
  clearTimer: (timer) => window.clearTimeout(timer),
})

const modeLabel = computed(() => {
  if (analysis.value?.mode === 'model') return '大模型分析'
  if (analysis.value?.mode === 'guardrail_fallback') return '模型结果未通过校验 · 可信降级'
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

function plainAnalysisText(value) {
  return formatAnalysisText(value).replace(/\[(F\d{3})\]/g, '').replace(/\s+/g, ' ').trim()
}

function sectionCitations(value) {
  return [...new Set([...String(value ?? '').matchAll(/\[(F\d{3})\]/g)].map((match) => match[1]))]
}

function sectionLead(value, fallback = '数据分析结论') {
  const text = plainAnalysisText(value)
  if (!text) return fallback
  const sentence = text.split(/[。；;！!？?]/)[0].trim()
  return sentence
}

function sectionBody(value) {
  const text = plainAnalysisText(value)
  const firstBreak = text.search(/[。；;！!？?]/)
  if (firstBreak < 0 || firstBreak >= text.length - 1) return text
  return text.slice(firstBreak + 1).trim()
}

function isLongSection(value) {
  return plainAnalysisText(value).length > 34
}

function isSectionExpanded(key) {
  return expandedSections.value.has(key)
}

function toggleSection(key) {
  const next = new Set(expandedSections.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedSections.value = next
}

const conclusionTitle = computed(() => sectionLead(analysis.value?.alerts || analysis.value?.overview, '生产运行分析已完成'))
const conclusionText = computed(() => plainAnalysisText(analysis.value?.overview || analysis.value?.features))

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
  activationTimer.cancel()
}

async function loadAnalysis(force = false, targetKey = requestKey()) {
  if (!activationTimer.isActive() || !props.ready || !props.reportDate) return
  requestController?.abort()
  requestController = new AbortController()
  const signal = requestController.signal
  const sequence = ++requestSequence
  activeRequestKey = targetKey
  loading.value = true
  error.value = ''
  try {
    const result = await analyzeReport({
      reportType: props.reportType,
      reportDate: props.reportDate,
      periodStart: props.periodStart || undefined,
      periodEnd: props.periodEnd || undefined,
      dataVersion: props.dataVersion || undefined,
      force,
    }, { signal })
    if (activationTimer.isActive() && !signal.aborted && sequence === requestSequence) {
      analysis.value = result
      displayedRequestKey = targetKey
    }
  } catch (exception) {
    if (exception.name !== 'AbortError' && sequence === requestSequence) error.value = exception.message
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      activeRequestKey = ''
      requestController = null
    }
  }
}

function scheduleAutomaticAnalysis() {
  clearScheduledAnalysis()
  if (!activationTimer.isActive()) return
  const targetKey = requestKey()
  if (!props.ready || !props.reportDate) {
    requestController?.abort()
    requestController = null
    requestSequence += 1
    activeRequestKey = ''
    loading.value = false
    // Pagination and cached-page deactivation can make ready false briefly even
    // though the report range and source version are unchanged. Preserve the
    // displayed result in that case; a changed request key still clears it.
    if (!props.reportDate || targetKey !== displayedRequestKey) {
      analysis.value = null
      displayedRequestKey = ''
      error.value = ''
    }
    return
  }
  if (targetKey === displayedRequestKey || (loading.value && targetKey === activeRequestKey)) return

  // The analysis API rebuilds the authoritative data_version and checks SQLite
  // before it can call the model. Abort an older response when the source changes.
  requestController?.abort()
  requestController = null
  requestSequence += 1
  activeRequestKey = ''
  loading.value = false
  void loadAnalysis(false, targetKey)
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

watch(analysis, () => {
  expandedSections.value = new Set()
})

function pauseAnalysis() {
  activationTimer.deactivate()
  clearScheduledAnalysis()
  requestController?.abort()
  requestController = null
  requestSequence += 1
  activeRequestKey = ''
  loading.value = false
}

onActivated(() => {
  activationTimer.activate()
  scheduleAutomaticAnalysis()
})
onDeactivated(pauseAnalysis)
onBeforeUnmount(pauseAnalysis)
</script>

<template>
  <section class="panel analysis-panel" aria-live="polite">
    <div class="analysis-panel-head">
      <div class="analysis-panel-title">
        <span class="analysis-panel-icon"><AppIcon name="sparkles" size="21" /></span>
        <div>
          <span class="analysis-kicker">AI REPORT ANALYSIS</span>
          <h2>智能分析</h2>
        </div>
      </div>
      <div class="analysis-actions">
        <span v-if="analysis" class="analysis-mode" :class="analysis.mode">
          {{ modeLabel }}<small v-if="analysis.cached">已缓存</small>
        </span>
        <button type="button" class="analysis-reload" :disabled="loading || !ready" @click="reloadAnalysis">
          <AppIcon v-if="!loading" name="activity" :size="14" />
          <span v-if="loading" class="spinner dark"></span>{{ loading ? '分析中' : '重新分析' }}
        </button>
      </div>
    </div>

    <div v-if="loading && !analysis" class="analysis-state">正在整理报表指标并生成分析…</div>
    <div v-else-if="error" class="analysis-error">
      <span>{{ error }}</span><button type="button" @click="retryAnalysis">重试</button>
    </div>
    <template v-else-if="analysis">
      <section class="analysis-conclusion" aria-labelledby="analysis-conclusion-title">
        <span class="analysis-conclusion__icon"><AppIcon name="sparkles" :size="31" tone="light" /></span>
        <div class="analysis-conclusion__copy">
          <span>分析结论 <small>AI 生成</small></span>
          <h3 id="analysis-conclusion-title">{{ conclusionTitle }}</h3>
          <p>{{ conclusionText }}</p>
        </div>
        <blockquote>数据不仅告诉我们发生了什么，<br>更帮助我们理解为什么会发生，<br>以及下一步应该做什么。</blockquote>
      </section>

      <div class="analysis-section-heading"><div><AppIcon name="chart" :size="19" /><h3>核心指标</h3></div><span>当前分析周期</span></div>
      <div v-if="analysis.facts?.length" class="analysis-facts">
        <article v-for="(fact, index) in analysis.facts" :key="fact.id">
          <span class="analysis-fact-icon"><AppIcon :name="['tasks','ruler','clock','chart','trend','database'][index % 6]" :size="19" /></span>
          <div>
            <span><b>{{ fact.id }}</b>{{ fact.label }}</span>
            <strong>{{ formatAnalysisText(fact.value) }}</strong>
          </div>
        </article>
      </div>

      <div class="analysis-section-heading analysis-section-heading--results"><div><AppIcon name="sparkles" :size="19" /><h3>多维分析结果</h3></div><span>以下结论均可追溯至指标依据</span></div>
      <div class="analysis-grid">
        <article v-for="section in [
          { key: 'overview', title: '生产概况', icon: 'chart' },
          { key: 'features', title: '数据特征', icon: 'trend' },
          { key: 'alerts', title: '异常与关注点', icon: 'alert' },
          { key: 'suggestions', title: '生产管理建议', icon: 'lightbulb' },
        ]" :key="section.key" :class="{ 'is-expanded': isSectionExpanded(section.key) }">
          <header><h3><AppIcon :name="section.icon" size="20" />{{ section.title }}</h3><div><sup v-for="citation in sectionCitations(analysis[section.key])" :key="citation" class="analysis-citation">{{ citation }}</sup></div></header>
          <div :id="`analysis-copy-${section.key}`" class="analysis-card-copy">
            <h4>{{ sectionLead(analysis[section.key], section.title) }}</h4>
            <p v-if="sectionBody(analysis[section.key])">{{ sectionBody(analysis[section.key]) }}</p>
          </div>
          <button
            v-if="isLongSection(analysis[section.key])"
            type="button"
            class="analysis-expand"
            :aria-expanded="isSectionExpanded(section.key)"
            :aria-controls="`analysis-copy-${section.key}`"
            @click="toggleSection(section.key)"
          >
            {{ isSectionExpanded(section.key) ? '收起' : '展开全文' }}
            <AppIcon name="arrow-down" :size="12" />
          </button>
        </article>
      </div>
      <details v-if="analysis.facts?.length" class="analysis-evidence">
        <summary><AppIcon name="database" :size="17" />查看可追溯数据依据（{{ analysis.facts.length }} 项）</summary>
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
.analysis-panel{--analysis-navy:#153f5d;--analysis-blue:#237fb7;--analysis-teal:#16a39d;--analysis-green:#2aa473;--analysis-red:#df5961;--analysis-indigo:#6674cf;overflow:hidden;margin-bottom:0!important;padding:0;border:1px solid #dbe6ec!important;border-radius:12px!important;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.05)!important}
.analysis-panel-head{min-height:66px;display:flex;align-items:center;justify-content:space-between;gap:24px;padding:13px 16px!important;border-bottom:1px solid #e5edf1;background:linear-gradient(90deg,#fff,#fbfdfe)!important}
.analysis-panel-title{display:flex;align-items:center;gap:11px}.analysis-panel-icon{width:38px!important;height:38px!important;display:grid;place-items:center;flex:0 0 auto;color:var(--analysis-indigo);border-radius:50%;background:#eef0fb}
.analysis-panel-head h2{margin:2px 0 0!important;color:#153c58!important;font-size:17px!important}.analysis-kicker{color:#177f96;font-size:9px!important;font-weight:800;letter-spacing:.12em}
.analysis-actions{display:flex;align-items:center;gap:10px;flex-shrink:0}.analysis-actions button,.analysis-error button{min-height:34px!important;padding:0 12px!important;border-radius:7px;font-size:11px!important}
.analysis-mode{display:inline-flex;align-items:center;gap:8px;padding:7px 11px;color:#986320;border:1px solid #eed8b5;border-radius:999px;background:#fff5e8;font-weight:700;white-space:nowrap}.analysis-mode.model{color:#19845f;border-color:#c5e2d3;background:#eaf7f1}.analysis-mode.simulated_fallback{color:#c24b54;border-color:#efd4d7;background:#fff0f1}.analysis-mode.guardrail_fallback{color:#986320;border-color:#eed8b5;background:#fff5e8}.analysis-mode small{padding-left:8px;border-left:1px solid currentColor;font-size:10px;opacity:.72}
.analysis-reload{display:inline-flex;align-items:center;justify-content:center;gap:7px;color:#315267;border-color:#c9dbe4;background:#f8fbfc}.analysis-reload:hover:not(:disabled){color:#fff;border-color:#0a6289;background:linear-gradient(135deg,#07557c,#087fa0)}
.analysis-state{padding:42px 28px;color:#607c91;text-align:center;background:#fff}.analysis-error{display:flex;align-items:center;justify-content:center;gap:14px;margin:22px 28px;padding:16px;color:#b53e48;background:#fff0f1}
.analysis-conclusion{display:grid;grid-template-columns:58px minmax(0,1fr) minmax(250px,.42fr);align-items:center;gap:18px;margin:12px 15px;padding:15px 18px;border:1px solid #c9e1eb;border-radius:9px;background:linear-gradient(105deg,#eff7fb,#f8fbfc 58%,#eef7fb);box-shadow:0 7px 18px rgba(22,62,89,.055)}
.analysis-conclusion__icon{width:50px;height:50px;display:grid;place-items:center;border-radius:50%;background:linear-gradient(145deg,var(--analysis-blue),#438fc6);box-shadow:0 0 0 6px rgba(35,127,183,.11),0 7px 16px rgba(22,62,89,.14)}.analysis-conclusion__copy{min-width:0}.analysis-conclusion__copy>span{color:#31536b;font-size:10px;font-weight:700}.analysis-conclusion__copy>span small{margin-left:8px;padding:3px 7px;color:var(--analysis-blue);border-radius:999px;background:#e3f1f8;font-size:8px}.analysis-conclusion h3{margin:5px 0 4px;color:var(--analysis-navy);font-size:17px;line-height:1.35}.analysis-conclusion p{margin:0;color:#607c91;font-size:10px;line-height:1.6}.analysis-conclusion blockquote{margin:0;padding:2px 0 2px 20px;color:#607c91;border-left:1px solid #c5d7e0;font-size:9px;line-height:1.75}
.analysis-section-heading{min-height:38px;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:0 15px;color:#124e74}.analysis-section-heading>div{display:flex;align-items:center;gap:8px}.analysis-section-heading h3{margin:0;color:#163e5a;font-size:14px}.analysis-section-heading>span{color:#507087;font-size:9px}.analysis-section-heading--results{margin-top:4px}
.analysis-facts{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px!important;padding:0 15px 9px!important}.analysis-facts article{--fact-color:var(--analysis-blue);--fact-soft:#eaf5fb;position:relative;min-width:0;min-height:104px;display:grid;grid-template-columns:29px minmax(0,1fr);align-items:center;gap:8px;overflow:hidden;padding:11px 10px!important;border:1px solid #dbe6ec;border-top:2px solid var(--fact-color);border-radius:11px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.055)}
.analysis-facts article:nth-child(2){--fact-color:var(--analysis-teal);--fact-soft:#e8f7f5}.analysis-facts article:nth-child(3){--fact-color:var(--analysis-indigo);--fact-soft:#eef0fb}.analysis-facts article:nth-child(4){--fact-color:#438fc6;--fact-soft:#eaf3fa}.analysis-facts article:nth-child(5){--fact-color:#d99132;--fact-soft:#fff5e8}.analysis-facts article:nth-child(6){--fact-color:#438fc6;--fact-soft:#eaf3fa}.analysis-facts article>div{min-width:0}.analysis-fact-icon{width:28px;height:28px;display:grid!important;place-items:center;color:var(--fact-color)!important;border-radius:7px;background:var(--fact-soft)}.analysis-facts span:not(.analysis-fact-icon){display:block;overflow:hidden;color:#254760;font-size:10px!important;text-overflow:ellipsis;white-space:nowrap}.analysis-facts span b{float:right;margin:0;padding:2px 5px;color:var(--analysis-blue);border:1px solid #cfe1e9;border-radius:4px;background:#edf7fb;font-size:8px}.analysis-facts strong{display:block;margin-top:10px;overflow:hidden;color:#173f64;font-size:18px!important;text-overflow:ellipsis;white-space:nowrap}
.analysis-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:9px!important;padding:0 15px 11px!important}.analysis-grid article{min-height:112px!important;padding:13px 15px!important;border:1px solid #d8e7ed!important;border-radius:8px!important;background:#eff6fc!important}.analysis-grid article:nth-child(2){border-color:#d5e8e7!important;background:#eff8fb!important}.analysis-grid article:nth-child(3){border-color:#efd9db!important;background:#fff0f0!important}.analysis-grid article:nth-child(4){border-color:#d6e7df!important;background:#eef8f4!important}.analysis-grid article>header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}.analysis-grid h3{display:flex;align-items:center;gap:7px;margin:0 0 7px;color:var(--analysis-blue);font-size:12px!important}.analysis-card-copy{min-width:0}.analysis-grid h4{display:-webkit-box;margin:0 0 5px;overflow:hidden;color:#193c57;font-size:13px;line-height:1.45;-webkit-box-orient:vertical;-webkit-line-clamp:2}.analysis-grid article:nth-child(2) h3{color:var(--analysis-teal)}.analysis-grid article:nth-child(3) h3{color:var(--analysis-red)}.analysis-grid article:nth-child(4) h3{color:var(--analysis-green)}.analysis-grid p{display:-webkit-box;margin:0;overflow:hidden;color:#60798b!important;font-size:10px!important;line-height:1.65!important;overflow-wrap:anywhere;white-space:pre-wrap;-webkit-box-orient:vertical;-webkit-line-clamp:2}.analysis-grid article.is-expanded h4,.analysis-grid article.is-expanded p{display:block;overflow:visible;-webkit-line-clamp:unset}.analysis-expand{min-height:24px!important;display:inline-flex;align-items:center;gap:3px;margin:7px 0 -3px;padding:0!important;border:0!important;background:transparent!important;box-shadow:none!important;color:#1b7188!important;font-size:9px!important;font-weight:700}.analysis-expand svg{transition:transform .18s ease}.analysis-grid article.is-expanded .analysis-expand svg{transform:rotate(180deg)}.analysis-expand:hover{color:#0c587a!important}.analysis-expand:focus-visible{outline:2px solid rgba(35,127,183,.32);outline-offset:3px}.analysis-citation{display:inline-block;margin:0 2px;padding:1px 4px;color:#1b7188;border:1px solid #b8dbe3;border-radius:4px;background:#eef8fa;font-size:8px;font-weight:800;line-height:1.2;vertical-align:middle;white-space:nowrap}
.analysis-evidence{margin:0 15px 9px!important;border:1px solid #dbe6ec;border-radius:8px;background:#f3f8fa}.analysis-evidence summary{min-height:39px;display:flex;align-items:center;gap:9px;padding:0 15px;color:#14597e;cursor:pointer;font-size:11px;font-weight:700}.evidence-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;border-top:1px solid #e1e9ed;background:#e1e9ed}.evidence-list article{display:grid;gap:4px;padding:12px 14px;background:#fff}.evidence-list article strong{color:#28465c;font-size:11px}.evidence-list article span{color:#0e5277;font-size:10px;font-weight:700}.evidence-list article small{color:#718697;font-size:9px;line-height:1.5}.analysis-time{margin:0;padding:0 15px 11px!important;color:#718697;text-align:right;font-size:9px!important}
@media(max-width:1180px){.analysis-facts{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:800px){.analysis-panel-head{align-items:flex-start;flex-direction:column}.analysis-actions{width:100%;justify-content:space-between}.analysis-conclusion{grid-template-columns:50px 1fr}.analysis-conclusion blockquote{grid-column:1/-1;padding:9px 0 0;border-top:1px solid #7199ad;border-left:0}.analysis-grid{grid-template-columns:1fr!important}.evidence-list{grid-template-columns:1fr}}
@media(max-width:720px){.analysis-actions{display:grid;grid-template-columns:1fr}.analysis-mode,.analysis-reload{width:100%;justify-content:center}.analysis-conclusion{margin:10px 12px;padding:14px}.analysis-conclusion__icon{width:44px;height:44px}.analysis-section-heading{padding:0 12px}.analysis-facts{grid-template-columns:repeat(2,minmax(0,1fr));padding:0 12px 9px!important}.analysis-grid{padding:0 12px 11px!important}.analysis-evidence{margin:0 12px 9px!important}}
@media(max-width:440px){.analysis-facts{grid-template-columns:1fr}}
</style>
