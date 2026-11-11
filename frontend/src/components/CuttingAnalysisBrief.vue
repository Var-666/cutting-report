<script setup lang="ts">
import type { AnalysisBrief, AnalysisFact, BriefFinding, BusinessReference, LeadershipPeriodRecord, LeadershipReport } from '../api/generated/types.gen'
import { computed, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCollapse, NCollapseItem, NModal } from 'naive-ui'
import AppIcon from './AppIcon.vue'
import AppReportTable from './AppReportTable.vue'
import MetricSparkline from './MetricSparkline.vue'
import { getDeviceId } from '../api/deviceContext.ts'
import { analysisTaskLocation } from '../utils/analysisReferences.ts'

const props = defineProps<{
  brief: AnalysisBrief
  facts: AnalysisFact[]
  generatedAt?: string | null
  leadershipReport?: LeadershipReport | null
}>()
const router = useRouter()
const selected = shallowRef<BriefFinding | null>(null)
const referencePage = shallowRef(1)
const visibleReferences = computed(() => selected.value?.references.slice((referencePage.value - 1) * 10, referencePage.value * 10) || [])
const taskCount = computed(() => selected.value?.references.filter(reference => reference.kind === 'task').length || 0)
watch(selected, () => { referencePage.value = 1 })
watch(() => props.brief, () => { selected.value = null })

const lengthFormat = new Intl.NumberFormat('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
const leadershipMetrics = {
  F001: { field: 'task_count', icon: 'tasks' },
  F002: { field: 'cut_length_m', icon: 'ruler' },
  F003: { field: 'work_time_seconds', icon: 'clock' },
  F007: { field: 'groove_length_m', icon: 'ruler' },
} as const
const reportMetricIcons = {
  workshop: { F001: 'tasks', F002: 'ruler', F003: 'clock', F004: 'check' },
  equipment: { F001: 'tasks', F002: 'clock', F003: 'wind', F004: 'zap' },
} satisfies Record<string, Record<string, string>>

function duration(seconds: number) {
  const whole = Math.round(seconds)
  return [Math.floor(whole / 3600), Math.floor(whole / 60) % 60, whole % 60]
    .map(part => String(part).padStart(2, '0')).join(':')
}

function metricNote(field: keyof LeadershipPeriodRecord, report: LeadershipReport, sampleDays: number) {
  const current = report.current_month_total
  if (field === 'work_time_seconds' && sampleDays) return { label: '有记录日均', value: duration(current.work_time_seconds / sampleDays) }
  if (field === 'groove_length_m' && sampleDays) return { label: '有记录日均', value: `${lengthFormat.format(current.groove_length_m / sampleDays)} 米` }
  if (field !== 'task_count' && field !== 'cut_length_m') return null
  const scope = props.brief.scope
  const previous = scope.comparison_start === report.comparison_start && scope.comparison_end === report.comparison_end
    ? report.comparison_total : null
  if (!scope.comparison_start || !previous || previous[field] <= 0) return null
  const change = (current[field] / previous[field] - 1) * 100
  return {
    label: scope.period_mode === 'month' ? '较上月' : '较对比周期',
    value: `${change > 0 ? '+' : ''}${change.toFixed(1)}%`,
    direction: change < 0 ? 'down' : change > 0 ? 'up' : '',
  }
}

const factById = computed(() => new Map(props.facts.map(fact => [fact.id, fact])))
const metrics = computed(() => {
  const kind = props.brief.scope.report_type
  const isLeadership = kind === 'leadership'
  const primary = Object.keys(isLeadership ? leadershipMetrics : reportMetricIcons[kind])
  const ids = primary.some(id => factById.value.has(id)) ? primary : props.brief.key_metric_ids
  const report = isLeadership ? props.leadershipReport : null
  const rows = report ? [...report.daily_records].sort((a, b) => a.period_start.localeCompare(b.period_start)) : []
  const sampleDays = rows.filter(row => row.task_count > 0).length
  const icons: Record<string, string> = isLeadership ? {} : reportMetricIcons[kind]
  return ids.flatMap(id => {
    const fact = factById.value.get(id)
    if (!fact) return []
    const definition = isLeadership ? leadershipMetrics[id as keyof typeof leadershipMetrics] : undefined
    const note = report && definition ? metricNote(definition.field, report, sampleDays) : null
    const values = definition ? rows.map(row => row[definition.field]) : []
    const value = String(fact.value ?? '—')
    const parts = /^([\d,.]+)\s+(\S+)$/.exec(value.trim())
    return [{ id, label: fact.label, value: parts?.[1] ?? value, unit: parts?.[2], icon: definition?.icon ?? icons[id] ?? 'chart', values, note }]
  })
})
const summaryLines = computed(() => props.brief.summary.split(/\n+/).map(line => line.trim()).filter(Boolean))
const periodNotice = computed(() => props.brief.data_quality.find(item => item.includes('当前统计周期尚未结束')))
const findingStyles: Record<string, { label: string; icon: string; tone: string }> = {
  C_comparison: { label: '生产规模变化', icon: 'trend', tone: 'info' },
  C_project: { label: '工程任务分布', icon: 'grid', tone: 'info' },
  C_project_missing: { label: '工程信息缺口', icon: 'alert', tone: 'warning' },
  C_confirmation: { label: '确认状态', icon: 'tasks', tone: 'warning' },
  C_energy_quality: { label: '能耗记录', icon: 'zap', tone: 'warning' },
}
const findings = computed(() => props.brief.findings.map(finding => {
  const appearance = findingStyles[finding.candidate_id]
  const decline = props.brief.scope.report_type === 'leadership' && finding.candidate_id === 'C_comparison'
    && String(factById.value.get('F004')?.value ?? '').startsWith('下降')
  return {
    finding,
    appearance,
    tone: decline ? 'brief-tone-error' : appearance?.tone === 'warning' ? 'brief-tone-warning' : '',
    decline,
  }
}))
const linkedFindings = computed(() => findings.value.filter(item => item.finding.references.length))
const recordCount = computed(() => new Set(linkedFindings.value.flatMap(({ finding }) =>
  finding.references.map(reference => `${reference.kind}:${reference.id}`))).size)
const evidenceRows = computed(() => linkedFindings.value.map(({ finding }) => {
  const first = finding.references[0]
  const project = finding.references.length === 1 && first?.kind === 'project'
    ? props.leadershipReport?.project_distribution.find(row => row.project_name === first.id) : undefined
  const tasks = finding.references.filter(reference => reference.kind === 'task').length
  return {
    finding,
    label: finding.references.slice(0, 3).map(reference => reference.label).join('、'),
    count: project ? `${project.task_count} 项` : tasks ? `${tasks} 项` : '—',
    totals: project ? `切割长度 ${lengthFormat.format(project.cut_length_m)} 米 · 切割时长 ${duration(project.work_time_seconds)}` : '—',
    detail: finding.title,
  }
}))
const selectedProject = computed(() => selected.value?.references.length === 1
  && selected.value.references[0]?.kind === 'project'
  ? evidenceRows.value.find(row => row.finding === selected.value) : undefined)
const evidenceColumns = [
  { key: 'c0', title: '关联对象', align: 'left' },
  { key: 'c1', title: '关联记录', align: 'left', ellipsis: false },
  { key: 'c2', title: '任务数', align: 'right' },
  { key: 'c3', title: '关键数据', align: 'left' },
  { key: 'c4', title: '说明', align: 'left' },
] as const
const referenceColumns = [
  { key: 'label', title: '关联对象', align: 'left' },
  { key: 'production_date', title: '生产日期', align: 'left' },
  { key: 'project_code', title: '工程号', align: 'left' },
  { key: 'program_no', title: '程序号', align: 'left' },
  { key: 'confirmation', title: '工人确认', align: 'left' },
  { key: 'energy_status', title: '能耗记录状态', align: 'left' },
  { key: 'c6', title: '操作', align: 'center', ellipsis: false },
] as const

function canOpen(reference: BusinessReference) {
  return Boolean(analysisTaskLocation(reference, getDeviceId()))
}

function openTask(reference: BusinessReference) {
  const location = analysisTaskLocation(reference, getDeviceId())
  if (!location) return
  selected.value = null
  void router.push(location)
}
</script>

<template>
  <div class="cutting-brief">
    <section class="brief-conclusion" aria-label="分析结论">
      <p class="brief-period">{{ brief.scope.line_name }} · {{ brief.scope.period_start }} 至 {{ brief.scope.period_end }}</p>
      <h3>{{ brief.headline }}</h3>
      <ul class="brief-summary">
        <li v-for="(line, index) in summaryLines" :key="index">{{ line }}</li>
      </ul>
    </section>

    <dl v-if="metrics.length" class="brief-metrics" aria-label="关键指标">
      <div v-for="metric in metrics" :key="metric.id" class="brief-metric">
        <span class="brief-metric-icon"><AppIcon :name="metric.icon" :size="24" tone="current" /></span>
        <dt>{{ metric.label }}</dt>
        <dd>{{ metric.value }}<small v-if="metric.unit">{{ metric.unit }}</small></dd>
        <div v-if="metric.note || metric.values.length > 1" class="brief-metric-foot">
          <span v-if="metric.note" class="brief-metric-note">
            {{ metric.note.label }}
            <strong :class="metric.note.direction">{{ metric.note.value }}</strong>
          </span>
          <MetricSparkline v-if="metric.values.length > 1" class="brief-sparkline" :class="{ 'brief-sparkline--groove': metric.id === 'F007' }"
            :values="metric.values" :label="`${metric.label}每日趋势`" :variant="metric.id === 'F001' ? 'bar' : 'line'" />
        </div>
      </div>
    </dl>

    <section v-if="findings.length || periodNotice" aria-label="关键发现">
      <h3 class="brief-section-title">关键发现</h3>
      <div class="brief-findings">
        <article v-for="{ finding, appearance, tone, decline } in findings" :key="finding.candidate_id"
          class="brief-finding" :class="tone">
          <header class="brief-card-heading">
            <AppIcon :name="appearance?.icon || 'activity'" :size="22" tone="current" />
            <h4>{{ appearance?.label || finding.title }}</h4>
            <span v-if="decline" class="brief-status">任务数下降</span>
          </header>
          <p v-if="appearance" class="brief-finding-title">{{ finding.title }}</p>
          <p class="brief-card-copy">{{ finding.detail }}</p>
        </article>
        <article v-if="periodNotice" class="brief-finding brief-tone-warning">
          <header class="brief-card-heading">
            <AppIcon name="calendar" :size="22" tone="current" />
            <h4>周期说明</h4>
            <span class="brief-status">周期未结束</span>
          </header>
          <p class="brief-card-copy">{{ periodNotice }}</p>
        </article>
      </div>
    </section>

    <section v-if="brief.actions.length" aria-label="建议跟进">
      <h3 class="brief-section-title">建议跟进</h3>
      <div class="brief-followup">
        <article v-for="(action, index) in brief.actions" :key="`${action.candidate_id}:${index}`" class="brief-action">
          <span class="brief-action-icon"><AppIcon name="check" :size="23" tone="current" /></span>
          <div><h4>{{ action.title }}</h4><p class="brief-card-copy">{{ action.detail }}</p></div>
        </article>
      </div>
    </section>

    <NCollapse class="brief-evidence" :default-expanded-names="['records']">
      <NCollapseItem v-if="evidenceRows.length" name="records">
        <template #header>
          <span class="brief-evidence-title"><AppIcon name="database" :size="17" />分析依据 / 关联记录 <span>（{{ recordCount }} 项）</span></span>
        </template>
        <AppReportTable :columns="evidenceColumns" :items="evidenceRows" :paginate="false"
          :row-key="row => row.finding.candidate_id" aria-label="分析关联记录">
          <template #c0="{ row }"><span class="brief-record-label">{{ row.label }}</span></template>
          <template #c1="{ row }"><NButton text type="info" @click="selected = row.finding">查看记录（{{ row.finding.reference_count }} 项）</NButton></template>
          <template #c2="{ row }">{{ row.count }}</template>
          <template #c3="{ row }">{{ row.totals }}</template>
          <template #c4="{ row }">{{ row.detail }}</template>
        </AppReportTable>
      </NCollapseItem>
      <NCollapseItem :title="evidenceRows.length ? '模型输入与数据说明' : '分析依据'" name="evidence">
        <p class="brief-card-copy">统计周期：{{ brief.scope.period_start }} 至 {{ brief.scope.period_end }}</p>
        <p v-if="brief.scope.comparison_start" class="brief-card-copy">比较周期：{{ brief.scope.comparison_start }} 至 {{ brief.scope.comparison_end }}</p>
        <ul class="brief-quality"><li v-for="item in brief.data_quality" :key="item">{{ item }}</li></ul>
        <dl class="brief-facts">
          <div v-for="fact in facts" :key="fact.id || fact.label"><dt>{{ fact.label }} · {{ fact.value }}</dt><dd>{{ fact.source }}</dd></div>
        </dl>
      </NCollapseItem>
    </NCollapse>

    <NModal :show="Boolean(selected)" preset="card" title="关联业务记录" class="brief-record-modal"
      style="width:min(1100px, calc(100vw - 32px))" @update:show="shown => { if (!shown) selected = null }">
      <template v-if="selected">
        <p class="brief-card-copy">分析时记录，共 {{ selected.reference_count }} 项关联<span v-if="taskCount">，其中任务 {{ taskCount }} 项</span>。生成时间：{{ generatedAt || '--' }}。</p>
        <div v-if="selectedProject" class="brief-project-record">
          <strong>{{ selectedProject.label }}</strong>
          <p class="brief-card-copy">{{ selected.detail }}</p>
          <p v-if="selectedProject.totals !== '—'" class="brief-card-copy">任务数 {{ selectedProject.count }} · {{ selectedProject.totals }}</p>
        </div>
        <AppReportTable v-else :columns="referenceColumns" :items="visibleReferences" :total="selected.references.length" :page="referencePage"
          :page-sizes="[]" @page="referencePage = $event" :row-key="row => `${row.kind}:${row.id}`" :page-size="10">
          <template #c6="{ row }"><NButton v-if="canOpen(row)" text type="primary" @click="openTask(row)">查看当前任务</NButton><span v-else>—</span></template>
        </AppReportTable>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.cutting-brief {
  --brief-accent: var(--analysis-accent);
  display:grid; gap:16px; padding:12px 14px 16px; min-width:0;
  color:var(--text-color-1); font-size:var(--font-body); overflow-wrap:anywhere;
}
.brief-conclusion,.brief-metric,.brief-finding,.brief-action,.brief-evidence {
  min-width:0; border:1px solid var(--theme-border-color); border-radius:var(--report-panel-radius);
  background:var(--analysis-surface);
}
.brief-conclusion {
  position:relative; isolation:isolate; overflow:hidden; padding:16px;
  border-color:color-mix(in srgb, var(--brief-accent) 22%, var(--analysis-surface));
  background:linear-gradient(105deg, var(--analysis-hero-start), var(--analysis-hero-end));
}
.brief-conclusion::before {
  content:''; position:absolute; z-index:-1; inset:0 0 0 auto; width:34%;
  background:linear-gradient(95deg, var(--analysis-hero-end) 0%, transparent 46%),
    url('../assets/home-laser-cutting.webp') 78% 57% / auto 165% no-repeat;
}
.brief-period { margin:0 0 7px; color:var(--text-color-2); font-size:var(--font-auxiliary); }
.brief-conclusion h3,.brief-section-title { position:relative; margin:0; padding-left:12px; font-weight:700; }
.brief-conclusion h3 { max-width:68%; font-size:21px; line-height:1.35; }
.brief-conclusion h3::before,.brief-section-title::before {
  content:''; position:absolute; inset:.15em auto .15em 0; width:4px; border-radius:3px; background:var(--brief-accent);
}
.brief-summary { max-width:68%; margin:8px 0 0; padding-left:24px; line-height:1.65; }
.brief-summary li::marker { color:var(--brief-accent); }
.brief-metrics { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%,170px),1fr)); gap:12px; margin:0; }
.brief-metric { display:grid; grid-template-columns:40px minmax(0,1fr); column-gap:12px; align-content:start; padding:14px 12px; box-shadow:var(--analysis-shadow); }
.brief-metric-icon,.brief-action-icon {
  display:grid; place-items:center; width:40px; height:40px; border-radius:9px;
  color:var(--brief-accent); background:color-mix(in srgb, var(--brief-accent) 11%, var(--analysis-surface));
}
.brief-metric-icon { grid-row:1/3; }
.brief-metric dt { font-size:var(--font-auxiliary); font-weight:600; }
.brief-metric dd { margin:2px 0 0; font-size:var(--font-kpi-value); font-weight:750; line-height:1.16; font-variant-numeric:tabular-nums; }
.brief-metric dd small { margin-left:5px; font-size:var(--font-auxiliary); font-weight:600; }
.brief-metric-foot { grid-column:1/-1; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:4px 8px; margin-top:10px; }
.brief-metric-note { display:flex; align-items:baseline; flex-wrap:wrap; gap:4px; padding:3px 8px; border-radius:999px; background:var(--analysis-panel-bg); color:var(--text-color-2); font-size:var(--font-auxiliary); }
.brief-metric-note strong { color:var(--text-color-1); }
.brief-metric-note .up { color:color-mix(in srgb, rgb(var(--success-color)) 75%, var(--text-color-1)); }
.brief-metric-note .down { color:color-mix(in srgb, rgb(var(--error-color)) 75%, var(--text-color-1)); }
.brief-sparkline { width:68px; height:24px; margin-left:auto; --kpi-color:var(--brief-accent); }
.brief-sparkline--groove { --kpi-color:rgb(var(--success-color)); }
.brief-section-title { margin-bottom:8px; font-size:var(--font-section-title); }
.brief-findings,.brief-followup { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:12px; }
.brief-findings:has(> :nth-child(3)) { grid-template-columns:repeat(3, minmax(0,1fr)); }
.brief-findings:has(> :only-child),.brief-followup:has(> :only-child) { grid-template-columns:1fr; }
.brief-finding { display:flex; flex-direction:column; gap:6px; padding:12px 14px; border-top:3px solid var(--brief-accent); }
.brief-tone-warning { --brief-accent:var(--analysis-warning-accent); }
.brief-tone-error { --brief-accent:var(--analysis-error-accent); }
.brief-card-heading { display:flex; align-items:center; flex-wrap:wrap; gap:6px 8px; }
.brief-card-heading .app-icon { flex:none; color:var(--brief-accent); }
.brief-card-heading h4,.brief-action h4 { margin:0; color:var(--text-color-1); font-size:14px; font-weight:700; }
.brief-status,.brief-record-label {
  padding:2px 7px; border-radius:5px; background:color-mix(in srgb, var(--brief-accent) 12%, var(--analysis-surface));
  color:color-mix(in srgb, var(--brief-accent) 68%, var(--text-color-1));
  font-size:var(--font-auxiliary); font-weight:600; white-space:nowrap;
}
.brief-tone-error .brief-status { background:var(--brief-accent); color:var(--analysis-error-ink); }
.brief-tone-warning .brief-status { background:var(--brief-accent); color:var(--analysis-warning-ink); }
.brief-finding-title { margin:0; font-weight:700; line-height:1.55; }
.brief-card-copy { margin:0; color:var(--text-color-2); line-height:1.65; white-space:pre-wrap; }
.brief-action { display:flex; align-items:center; gap:12px; padding:12px 14px; border-left:3px solid var(--brief-accent); }
.brief-action-icon { flex:none; border-radius:50%; }
.brief-action > div { min-width:0; flex:1; }
.brief-action .brief-card-copy { margin-top:4px; }
.brief-evidence { padding:10px 12px; box-shadow:var(--analysis-shadow); }
.brief-evidence-title { display:flex; align-items:center; flex-wrap:wrap; gap:4px 7px; font-size:14px; font-weight:700; }
.brief-evidence-title > span { font-size:var(--font-auxiliary); font-weight:400; }
.brief-quality { padding-left:20px; line-height:1.65; }
.brief-facts { margin-bottom:0; }
.brief-facts div + div { margin-top:8px; }
.brief-facts dt { font-weight:600; }
.brief-facts dd { margin:3px 0 0; color:var(--text-color-2); }
.brief-project-record { display:grid; gap:6px; margin-top:12px; padding:12px; border-radius:var(--card-radius); background:rgb(var(--info-color) / .07); }
@container analysis-panel (max-width:850px) {
  .brief-metrics,.brief-findings:has(> :nth-child(3)) { grid-template-columns:repeat(2, minmax(0,1fr)); }
  .brief-followup { grid-template-columns:1fr; }
  .brief-conclusion::before { opacity:.26; width:44%; }
  .brief-conclusion h3,.brief-summary { max-width:100%; }
}
@container analysis-panel (max-width:540px) {
  .brief-conclusion { padding:12px; }
  .brief-conclusion::before { opacity:.2; width:48%; }
  .brief-conclusion h3,.brief-summary { max-width:100%; }
  .brief-conclusion h3 { font-size:var(--font-section-title); }
  .brief-findings,.brief-findings:has(> :nth-child(3)) { grid-template-columns:1fr; }
  .brief-metric { grid-template-columns:26px minmax(0,1fr); padding:8px; gap:4px 7px; }
  .brief-metric-icon { grid-row:1; width:26px; height:26px; }
  .brief-metric-icon .app-icon { width:19px; height:19px; }
  .brief-metric dt { align-self:center; }
  .brief-metric dd { grid-column:1/-1; font-size:18px; }
}
</style>
