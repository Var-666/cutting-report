<script setup>
import { computed, onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { getLeadershipReport, listReports } from '../api/reports'
import { session } from '../state/session'
import { reportPreload } from '../state/reportPreload.js'
import { reportToday } from '../api/reportDates.js'

const reportMonth = reportToday().slice(0, 7)

const loading = ref(true)
const overview = ref(null)
const recentReports = ref([])
const historyTotal = ref(0)
const error = ref('')

const total = computed(() => overview.value?.current_month_total || {
  task_count: 0,
  cut_length_m: 0,
  work_time_seconds: 0,
})
const activeDays = computed(() => overview.value?.daily_records?.length || 0)
const dataSourceLabel = computed(() => ({
  demo: '演示数据',
  excel: 'TASK.xlsx',
  sqlserver: 'SQL Server',
}[session.systemInfo?.data_source_mode] || '正在检查'))

const reportTypeText = {
  leadership: '领导报表',
  workshop: '车间报表',
  equipment: '设备报表',
  custom: '自定义报表',
}
const statusText = {
  pending: '等待中',
  querying: '查询中',
  calculating: '统计中',
  analyzing: '分析中',
  rendering: '渲染中',
  completed: '已完成',
  completed_with_warnings: '已完成·有提示',
  failed: '失败',
}

function formatNumber(value, digits = 3) {
  return new Intl.NumberFormat('zh-CN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value || 0))
}

function formatDuration(value) {
  const seconds = Math.max(0, Math.round(Number(value || 0)))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return `${hours}小时 ${String(minutes).padStart(2, '0')}分`
}

function formatDateTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '--'
}

function reportPeriod(item) {
  if (!item?.report_date) return '--'
  return item.report_type === 'leadership'
    ? item.report_date.slice(0, 7)
    : item.report_date
}

async function loadDashboard() {
  loading.value = true
  error.value = ''
  const [overviewResult, reportsResult] = await Promise.allSettled([
    getLeadershipReport(reportMonth),
    listReports({ page: 1, pageSize: 10 }),
  ])
  if (overviewResult.status === 'fulfilled') {
    overview.value = overviewResult.value
  } else {
    error.value = overviewResult.reason.message
  }
  if (reportsResult.status === 'fulfilled') {
    recentReports.value = reportsResult.value.items.slice(0, 5)
    historyTotal.value = reportsResult.value.total
  } else {
    error.value ||= reportsResult.reason.message
  }
  loading.value = false
}

onMounted(loadDashboard)
</script>

<template>
  <section class="home-hero">
    <div class="home-hero-copy">
      <span class="home-eyebrow">PRODUCTION COMMAND CENTER</span>
      <h1>生产管理首页</h1>
      <p>{{ session.user?.display_name }}，欢迎进入切割生产管理系统。从这里查看本月概况，并进入各类报表与管理工具。</p>
      <div class="home-hero-actions">
        <RouterLink class="button primary" to="/admin/reports/leadership">查看领导报表</RouterLink>
        <RouterLink class="button" to="/admin/reports/center">生成新报告</RouterLink>
      </div>
    </div>
    <aside class="home-system-card">
      <span>系统状态</span>
      <strong><i></i>服务正常</strong>
      <dl>
        <div><dt>当前数据源</dt><dd>{{ dataSourceLabel }}</dd></div>
        <div><dt>统计月份</dt><dd>{{ reportMonth }}</dd></div>
        <div><dt>系统版本</dt><dd>V{{ session.systemInfo?.version || '0.1.0' }}</dd></div>
      </dl>
    </aside>
  </section>

  <div v-if="error" class="global-error home-error">
    <strong>部分概览数据读取失败</strong><span>{{ error }}</span><button type="button" @click="loadDashboard">重试</button>
  </div>

  <div v-if="reportPreload.items.length" class="home-preload" role="status" aria-live="polite">
    <strong>报表预加载</strong>
    <span v-for="item in reportPreload.items" :key="item.key" :data-status="item.status">{{ item.label }}：{{ { loading: '加载中', ready: '已预加载', failed: '暂未加载，进入页面可重试' }[item.status] }}</span>
  </div>

  <section class="home-kpis" aria-label="本月生产概况">
    <article><span>本月完成任务</span><strong>{{ loading ? '--' : total.task_count }}</strong><small>项</small></article>
    <article><span>本月切割长度</span><strong>{{ loading ? '--' : formatNumber(total.cut_length_m) }}</strong><small>米</small></article>
    <article><span>本月切割时长</span><strong>{{ loading ? '--' : formatDuration(total.work_time_seconds) }}</strong><small>累计工时</small></article>
    <article><span>有生产记录日</span><strong>{{ loading ? '--' : activeDays }}</strong><small>天</small></article>
  </section>

  <section class="home-section">
    <div class="home-section-head">
      <div><span>REPORTS</span><h2>三类生产报表</h2></div>
      <p>报表页面均包含表格、图表和智能分析</p>
    </div>
    <div class="home-report-grid">
      <RouterLink class="home-report-card leadership" to="/admin/reports/leadership">
        <span class="home-card-index">01</span><div><small>MONTHLY OVERVIEW</small><h3>领导报表</h3><p>按月汇总任务、长度和工时，查看当月明细及十二个月趋势。</p></div><b>进入报表 →</b>
      </RouterLink>
      <RouterLink class="home-report-card workshop" to="/admin/reports/workshop">
        <span class="home-card-index">02</span><div><small>DAILY WORKSHOP</small><h3>车间报表</h3><p>查看当日任务与工人确认状态，跟踪未认领任务和生产明细。</p></div><b>进入报表 →</b>
      </RouterLink>
      <RouterLink class="home-report-card equipment" to="/admin/reports/equipment">
        <span class="home-card-index">03</span><div><small>DAILY EQUIPMENT</small><h3>设备报表</h3><p>查看设备生产明细、切割时长和能耗数据，按生产时间排列。</p></div><b>进入报表 →</b>
      </RouterLink>
    </div>
  </section>

  <section class="home-bottom-grid">
    <article class="panel home-history">
      <div class="home-panel-head">
        <div><span>RECENT OUTPUT</span><h2>最近生成报告</h2></div>
        <RouterLink to="/admin/reports/center">查看全部 {{ historyTotal ? `(${historyTotal})` : '' }}</RouterLink>
      </div>
      <div class="home-history-list">
        <div v-if="loading" class="home-empty">正在读取报告记录…</div>
        <div v-else-if="!recentReports.length" class="home-empty">暂无已生成报告，可从“生成新报告”开始。</div>
        <div v-for="item in recentReports" v-else :key="item.report_id" class="home-history-row">
          <span class="home-report-type">{{ item.report_title || reportTypeText[item.report_type] || '生产报表' }}</span>
          <span>{{ reportPeriod(item) }}</span>
          <span>{{ item.task_count ?? '--' }} 项</span>
          <span class="home-status" :data-status="item.status">{{ statusText[item.status] || item.status }}</span>
          <time>{{ formatDateTime(item.generated_at || item.created_at) }}</time>
          <a v-if="item.view_url" :href="item.view_url" target="_blank" rel="noopener">打开</a><span v-else>--</span>
        </div>
      </div>
    </article>

    <aside class="panel home-tools">
      <div class="home-panel-head"><div><span>MANAGEMENT</span><h2>快捷管理</h2></div></div>
      <RouterLink to="/admin/reports/center"><strong>报表生成与历史</strong><small>生成、归档和下载 HTML 报告</small><b>→</b></RouterLink>
      <RouterLink to="/admin/settings"><strong>系统设置</strong><small>数据库、报表设计和自动化集中管理</small><b>→</b></RouterLink>
      <RouterLink to="/admin/settings/database"><strong>数据源状态</strong><small>检查生产库、工人库和切割文件库</small><b>→</b></RouterLink>
    </aside>
  </section>
</template>

<style scoped>
.home-preload { display:flex; flex-wrap:wrap; gap:10px 22px; align-items:center; margin-bottom:20px; padding:12px 16px; background:#f4fafb; border:1px solid #d4e3e9; color:#63788a; font-size:12px; }
.home-preload strong { color:#123450; }.home-preload [data-status="ready"] { color:#18766b; }.home-preload [data-status="failed"] { color:#9b612c; }
.home-hero { min-height:250px; display:grid; grid-template-columns:minmax(0,1fr) 330px; align-items:center; gap:48px; padding:34px 0 28px; }
.home-eyebrow,.home-section-head span,.home-panel-head span { color:#168397; font-size:10px; font-weight:800; letter-spacing:2.4px; }
.home-hero h1 { margin:8px 0 10px; color:#123450; font-size:clamp(30px,4vw,46px); letter-spacing:1px; }
.home-hero p { max-width:720px; margin:0; color:#63788a; line-height:1.8; }
.home-hero-actions { display:flex; gap:10px; margin-top:24px; }
.home-system-card { padding:22px 24px; color:#fff; background:linear-gradient(135deg,#0a3e61,#0d6f85); box-shadow:0 18px 42px rgba(17,62,89,.18); }
.home-system-card > span { color:#9bcbd4; font-size:10px; letter-spacing:2px; }
.home-system-card > strong { display:flex; align-items:center; gap:9px; margin:8px 0 17px; font-size:20px; }
.home-system-card > strong i { width:9px; height:9px; border-radius:50%; background:#62d2a2; box-shadow:0 0 0 5px rgba(98,210,162,.13); }
.home-system-card dl { margin:0; border-top:1px solid rgba(255,255,255,.14); }
.home-system-card dl div { display:flex; justify-content:space-between; gap:16px; padding:9px 0; border-bottom:1px solid rgba(255,255,255,.1); }
.home-system-card dt { color:#9ec2ce; font-size:11px; }.home-system-card dd { margin:0; font-size:11px; font-weight:700; }
.home-error { margin-bottom:20px; }
.home-kpis { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:28px; }
.home-kpis article { min-height:130px; padding:20px 22px; border-top:3px solid #26a4b0; background:#fff; box-shadow:0 8px 24px rgba(20,54,78,.06); }
.home-kpis span { display:block; color:#6a7e8d; font-size:11px; }.home-kpis strong { display:inline-block; margin:13px 6px 0 0; color:#0b4b73; font-size:27px; font-variant-numeric:tabular-nums; }.home-kpis small { color:#8a9aa6; }
.home-section { margin-bottom:28px; }
.home-section-head,.home-panel-head { display:flex; align-items:flex-end; justify-content:space-between; gap:24px; margin-bottom:14px; }
.home-section-head h2,.home-panel-head h2 { margin:4px 0 0; color:#19354b; font-size:21px; }.home-section-head p { margin:0; color:#718391; font-size:11px; }
.home-report-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:15px; }
.home-report-card { position:relative; min-height:220px; display:flex; flex-direction:column; justify-content:space-between; overflow:hidden; padding:23px 24px 20px; color:#18364b; border:1px solid #d5e2e8; background:#fff; box-shadow:0 8px 24px rgba(20,54,78,.055); text-decoration:none; transition:transform .18s ease,box-shadow .18s ease; }
.home-report-card::before { content:""; position:absolute; top:0; right:0; left:0; height:4px; background:#176f9b; }.home-report-card.workshop::before { background:#2aa4a5; }.home-report-card.equipment::before { background:#d08a2a; }
.home-report-card:hover { transform:translateY(-3px); box-shadow:0 15px 34px rgba(20,54,78,.11); }
.home-card-index { position:absolute; top:14px; right:18px; color:#e1e9ed; font:44px Georgia,serif; }
.home-report-card small { color:#7a919f; font-size:9px; letter-spacing:1.6px; }.home-report-card h3 { margin:8px 0 9px; font-size:23px; }.home-report-card p { max-width:85%; margin:0; color:#667b8b; font-size:12px; line-height:1.75; }.home-report-card b { color:#0d6f9e; font-size:11px; }
.home-bottom-grid { display:grid; grid-template-columns:minmax(0,1.7fr) minmax(300px,.8fr); gap:18px; }
.home-history,.home-tools { min-width:0; padding:22px 24px; }.home-panel-head { align-items:center; margin-bottom:17px; }.home-panel-head a { color:#0d6f9e; font-size:11px; text-decoration:none; }
.home-history-list { border-top:1px solid #dce5ea; }.home-history-row { min-height:51px; display:grid; grid-template-columns:1.2fr .75fr .55fr .9fr 1.05fr 36px; align-items:center; gap:10px; color:#607483; border-bottom:1px solid #e5ebef; font-size:10px; }.home-report-type { overflow:hidden; color:#1b3f57; text-overflow:ellipsis; white-space:nowrap; font-weight:700; }.home-history-row time { font-variant-numeric:tabular-nums; }.home-history-row a { color:#0d6f9e; text-decoration:none; }
.home-status { justify-self:start; padding:3px 7px; color:#176e51; border-radius:999px; background:#e9f6f0; }.home-status[data-status="failed"] { color:#a33e35; background:#fff0ee; }.home-status[data-status="pending"],.home-status[data-status="querying"],.home-status[data-status="calculating"],.home-status[data-status="analyzing"],.home-status[data-status="rendering"] { color:#976515; background:#fff7e4; }
.home-empty { padding:40px 12px; color:#7d8e99; text-align:center; font-size:12px; }
.home-tools { display:flex; flex-direction:column; }.home-tools .home-panel-head { min-height:41px; }
.home-tools > a { position:relative; display:flex; flex-direction:column; padding:14px 34px 14px 0; color:#1c3c52; border-top:1px solid #dfe7ec; text-decoration:none; }.home-tools > a strong { font-size:12px; }.home-tools > a small { margin-top:4px; color:#718491; line-height:1.5; }.home-tools > a b { position:absolute; top:22px; right:4px; color:#1e8b9d; font-size:17px; }
@media (max-width:900px) { .home-hero { grid-template-columns:1fr; gap:20px; }.home-system-card { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:0 18px; }.home-system-card > strong { margin:4px 0; }.home-system-card dl { grid-column:1/-1; margin-top:12px; }.home-kpis { grid-template-columns:repeat(2,1fr); }.home-report-grid { grid-template-columns:1fr; }.home-report-card { min-height:180px; }.home-bottom-grid { grid-template-columns:1fr; } }
@media (max-width:560px) { .home-hero { padding-top:28px; }.home-hero-actions { align-items:stretch; flex-direction:column; }.home-system-card { display:block; }.home-kpis { grid-template-columns:1fr; }.home-section-head { align-items:flex-start; flex-direction:column; gap:4px; }.home-history,.home-tools { padding:18px 15px; }.home-history-row { grid-template-columns:1fr auto auto; padding:10px 0; }.home-history-row > :nth-child(3),.home-history-row time { display:none; } }
</style>
