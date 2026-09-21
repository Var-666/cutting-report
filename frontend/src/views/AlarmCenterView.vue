<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getAlarmStatus, listAlarmIncidents, listAlarmReports } from '../api/reports'
import heroImage from '../assets/home-laser-cutting.webp'
import AppIcon from '../components/AppIcon.vue'
import { formatChineseDateTime } from '../utils/dateDisplay.js'

const status = ref(null)
const incidents = ref([])
const reports = ref([])
const loading = ref(false)
const error = ref('')
const activeSeverity = ref('all')
const currentTime = ref(new Date())
let refreshTimer = null
let clockTimer = null
let requestController = null
let requestSequence = 0
let pageActive = false

const openIncidents = computed(() => incidents.value.filter((item) => item.status === 'open'))
const openCount = computed(() => openIncidents.value.length)
const completedCount = computed(() => reports.value.filter((item) => item.status === 'completed').length)
const severityLabels = { warning: '提示', high: '一般', critical: '严重' }
const reportStatusLabels = { generating: '生成中', completed: '已生成', failed: '生成失败' }
const monitorReady = computed(() => Boolean(status.value?.enabled && status.value?.worker_running))
const machineLabel = computed(() => status.value?.machine_nos?.join('、') || '未配置设备')

const severityTabs = computed(() => [
  { value: 'all', label: '全部', count: incidents.value.length },
  { value: 'critical', label: '严重', count: incidents.value.filter((item) => item.severity === 'critical').length },
  { value: 'high', label: '一般', count: incidents.value.filter((item) => item.severity === 'high').length },
  { value: 'warning', label: '提示', count: incidents.value.filter((item) => item.severity === 'warning').length },
])

const filteredIncidents = computed(() => activeSeverity.value === 'all'
  ? incidents.value
  : incidents.value.filter((item) => item.severity === activeSeverity.value))

const dominantAlarm = computed(() => {
  const source = openIncidents.value.length ? openIncidents.value : incidents.value
  if (!source.length) return null
  const counts = new Map()
  source.forEach((item) => {
    const key = item.alarm_class || item.alarm_desc || '其他异常'
    counts.set(key, (counts.get(key) || 0) + 1)
  })
  return [...counts.entries()].sort((left, right) => right[1] - left[1])[0]
})

const insightSummary = computed(() => openCount.value
  ? `当前有 ${openCount.value} 项告警未解除，其中严重告警 ${severityTabs.value[1].count} 项。`
  : '当前没有未解除告警，设备监控状态整体平稳。')

const insightCause = computed(() => dominantAlarm.value
  ? `${dominantAlarm.value[0]}出现 ${dominantAlarm.value[1]} 次，建议优先核查相关设备状态。`
  : '暂无足够告警样本，系统将持续分析异常类型与出现频率。')

function formatDateTime(value) {
  return formatChineseDateTime(value)
}

function formatShortDateTime(value) {
  if (!value) return '--'
  return formatDateTime(value).replace(/年|月/g, '-').replace('日', '')
}

function formatBytes(value) {
  if (!value) return '--'
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`
}

function durationLabel(item) {
  if (!item?.first_seen_at) return '--'
  const start = new Date(item.first_seen_at).getTime()
  const endValue = item.status === 'closed' ? (item.closed_at || item.last_seen_at) : currentTime.value
  const end = endValue instanceof Date ? endValue.getTime() : new Date(endValue).getTime()
  if (!Number.isFinite(start) || !Number.isFinite(end)) return '--'
  const minutes = Math.max(0, Math.floor((end - start) / 60_000))
  if (minutes < 60) return `${minutes || 1} 分钟`
  const hours = Math.floor(minutes / 60)
  const remain = minutes % 60
  return hours < 24 ? `${hours} 小时 ${remain} 分钟` : `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`
}

function possibleCause(item) {
  return item.alarm_class || item.alarm_desc || '等待进一步分析'
}

function reportName(item) {
  return item.pdf_filename || `${item.machine_no || '设备'}_告警报告_${String(item.created_at || '').slice(0, 10).replaceAll('-', '')}.pdf`
}

function linkedReport(item) {
  return reports.value.find((report) => report.report_id === item.pdf_report_id)
}

function relativeSyncTime(value) {
  if (!value) return '尚未完成检测'
  const diff = Math.max(0, currentTime.value.getTime() - new Date(value).getTime())
  const minutes = Math.floor(diff / 60_000)
  if (minutes < 1) return '刚刚完成'
  if (minutes < 60) return `${minutes} 分钟前`
  return `${Math.floor(minutes / 60)} 小时前`
}

function stopRefreshTimer() {
  if (!refreshTimer) return
  window.clearTimeout(refreshTimer)
  refreshTimer = null
}

function scheduleRefresh() {
  stopRefreshTimer()
  if (!pageActive) return
  refreshTimer = window.setTimeout(async () => {
    refreshTimer = null
    await loadData()
    scheduleRefresh()
  }, 60_000)
}

async function loadData() {
  if (!pageActive || requestController) return
  const controller = new AbortController()
  const sequence = ++requestSequence
  requestController = controller
  loading.value = true
  error.value = ''
  try {
    const [statusData, incidentData, reportData] = await Promise.all([
      getAlarmStatus({ signal: controller.signal }),
      listAlarmIncidents({ limit: 100 }, { signal: controller.signal }),
      listAlarmReports({ limit: 50 }, { signal: controller.signal }),
    ])
    if (!pageActive || controller.signal.aborted || sequence !== requestSequence) return
    status.value = statusData
    incidents.value = incidentData
    reports.value = reportData
    currentTime.value = new Date()
  } catch (exception) {
    if (exception.name !== 'AbortError' && pageActive && sequence === requestSequence) {
      error.value = exception.message
    }
  } finally {
    if (sequence === requestSequence) {
      requestController = null
      loading.value = false
    }
  }
}

onMounted(async () => {
  pageActive = true
  await loadData()
  scheduleRefresh()
  clockTimer = window.setInterval(() => { currentTime.value = new Date() }, 30_000)
})
onBeforeUnmount(() => {
  pageActive = false
  stopRefreshTimer()
  window.clearInterval(clockTimer)
  clockTimer = null
  requestSequence += 1
  requestController?.abort()
  requestController = null
  loading.value = false
})
</script>

<template>
  <div class="alarm-page">
    <section class="page-hero report-hero alarm-hero" :style="{ '--alarm-hero-image': `url(${heroImage})` }">
      <div class="alarm-hero__copy">
        <p class="eyebrow">INTELLIGENT ALARM CENTER</p>
        <h1>智能告警中心</h1>
        <p>按设定周期自动检测切割机运行状态，发现新的异常问题时生成实体 PDF 报告。</p>
      </div>
      <p class="alarm-hero__slogan"><strong>智能监控预警</strong><span>防患于未然</span></p>
      <div class="monitor-card" :class="{ ready: monitorReady }">
        <div class="monitor-card__head">
          <span class="monitor-pulse"><AppIcon :name="monitorReady ? 'check' : 'alert'" size="17" /></span>
          <p><small>监控设备状态</small><strong>{{ status?.enabled ? (status.worker_running ? '运行中' : '等待启动') : '未启用' }}</strong></p>
        </div>
        <dl>
          <div><dt>设备</dt><dd>{{ machineLabel }}</dd></div>
          <div><dt>最近检测</dt><dd>{{ formatDateTime(status?.last_success_at) }}</dd></div>
        </dl>
      </div>
    </section>

    <div v-if="error" class="global-error"><strong>告警数据读取失败</strong><span>{{ error }}</span><button @click="loadData">重试</button></div>
    <div v-if="status && !status.enabled" class="alarm-notice"><AppIcon name="alert" size="18" />告警监控尚未启用。请先在部署配置中开启监控服务。</div>

    <section class="leadership-kpis alarm-kpis" aria-label="告警监控概况">
      <article class="alarm-kpi kpi-sky">
        <span class="alarm-kpi__icon"><AppIcon name="factory" size="22" /></span>
        <div><span>监控设备<small>MONITORED DEVICES</small></span><p><strong>{{ status?.machine_nos?.length || 0 }}</strong> 台</p><em>{{ machineLabel }}</em></div>
      </article>
      <article class="alarm-kpi kpi-red">
        <span class="alarm-kpi__icon"><AppIcon name="alert" size="22" /></span>
        <div><span>当前告警<small>CURRENT ALARMS</small></span><p><strong>{{ openCount }}</strong> 项</p><em :class="{ safe: !openCount }">{{ openCount ? `严重 ${severityTabs[1].count} 项` : '状态正常' }}</em></div>
      </article>
      <article class="alarm-kpi kpi-indigo">
        <span class="alarm-kpi__icon"><AppIcon name="database" size="22" /></span>
        <div><span>已同步记录<small>SYNCED RECORDS</small></span><p><strong>{{ status?.last_alarm_id || 0 }}</strong> 条</p><em>生产告警游标</em></div>
      </article>
      <article class="alarm-kpi kpi-teal alarm-kpi--wide">
        <span class="alarm-kpi__icon"><AppIcon name="calendar" size="22" /></span>
        <div><span>最近检测时间<small>LATEST CHECK</small></span><p><strong>{{ formatShortDateTime(status?.last_success_at) }}</strong></p><em>{{ relativeSyncTime(status?.last_success_at) }}</em></div>
      </article>
      <article class="alarm-kpi kpi-green">
        <span class="alarm-kpi__icon"><AppIcon name="file" size="22" /></span>
        <div><span>已生成报告<small>GENERATED REPORTS</small></span><p><strong>{{ completedCount }}</strong> 份</p><em>实体 PDF 报告</em></div>
      </article>
    </section>

    <div class="alarm-content-grid">
      <section class="panel alarm-list">
        <div class="alarm-section-head">
          <div class="alarm-section-title"><span class="section-icon danger"><AppIcon name="bell" size="18" /></span><h2>告警事件<small>ALARM EVENTS</small></h2></div>
          <div class="alarm-tabs" role="tablist" aria-label="按告警级别筛选">
            <button v-for="tab in severityTabs" :key="tab.value" type="button" role="tab" :aria-selected="activeSeverity === tab.value" :class="{ active: activeSeverity === tab.value }" @click="activeSeverity = tab.value">{{ tab.label }}（{{ tab.count }}）</button>
          </div>
          <button class="alarm-refresh-button" type="button" :disabled="loading" :aria-busy="loading" @click="loadData">
            <span v-if="loading" class="spinner dark" aria-hidden="true"></span><AppIcon v-else name="activity" size="15" />{{ loading ? '刷新中' : '刷新数据' }}
          </button>
        </div>
        <div v-if="loading && !incidents.length" class="alarm-empty-state" role="status">
          <span class="spinner dark" aria-hidden="true"></span><strong>正在读取告警事件</strong>
        </div>
        <div v-else-if="!filteredIncidents.length" class="alarm-empty-state" role="status">
          <span class="empty-icon"><AppIcon name="check" size="24" /></span><strong>当前筛选下暂无告警</strong><span>{{ activeSeverity === 'all' ? '20kW 切割机当前无未解除告警，系统将持续监控。' : '当前级别暂无告警，系统将持续监控切割机运行状态。' }}</span>
        </div>
        <div v-else class="alarm-table-wrap">
          <table v-content-table aria-label="切割机告警事件">
            <thead><tr><th>告警级别</th><th>设备名称</th><th>异常类型</th><th>发现时间</th><th>持续时长</th><th>当前状态</th><th>可能原因</th><th>处理状态</th><th>操作</th></tr></thead>
            <tbody><tr v-for="item in filteredIncidents" :key="item.incident_id">
              <td><span class="severity-badge" :class="item.severity"><span>!</span>{{ severityLabels[item.severity] || item.severity }}</span></td>
              <td>{{ item.machine_no || '--' }}</td>
              <td><strong class="alarm-name">{{ item.alarm_desc || '--' }}</strong><small>{{ item.alarm_code || item.alarm_type || '--' }}</small></td>
              <td>{{ formatShortDateTime(item.first_seen_at) }}</td>
              <td>{{ durationLabel(item) }}</td>
              <td><span class="live-state" :class="item.status"><i></i>{{ item.status === 'open' ? '异常' : '正常' }}</span></td>
              <td class="cause-cell">{{ possibleCause(item) }}</td>
              <td><span class="incident-state" :class="item.status">{{ item.status === 'open' ? '待处理' : '已处理' }}</span></td>
              <td class="report-actions"><a v-if="linkedReport(item)?.view_url" :href="linkedReport(item).view_url" target="_blank" rel="noopener">查看</a><span v-else>{{ item.pdf_report_id ? '已归档' : '待生成' }}</span></td>
            </tr></tbody>
          </table>
        </div>
      </section>

      <aside class="alarm-insights" aria-labelledby="alarm-insights-title">
        <header><div class="alarm-section-title"><span class="section-icon"><AppIcon name="sparkles" size="18" /></span><h2 id="alarm-insights-title">AI 告警洞察<small>AI INSIGHTS</small></h2></div><span>实时汇总</span></header>
        <div class="insight-card insight-card--blue"><span><AppIcon name="chart" size="24" /></span><p><strong>当前告警概况</strong><small>{{ insightSummary }}</small></p></div>
        <div class="insight-card insight-card--red"><span><AppIcon name="alert" size="24" /></span><p><strong>主要问题分析</strong><small>{{ insightCause }}</small></p></div>
        <div class="insight-card insight-card--amber"><span><AppIcon name="lightbulb" size="24" /></span><p><strong>建议检查项</strong><small>1. 检查电源及急停回路<br>2. 检查气压与冷却系统<br>3. 核对设备当前运行参数</small></p></div>
      </aside>
    </div>

    <section class="panel alarm-list">
      <div class="alarm-section-head report-head">
        <div class="alarm-section-title"><span class="section-icon"><AppIcon name="file" size="18" /></span><h2>实体告警报告<small>ALARM REPORTS</small></h2></div>
        <span class="report-count">共 {{ reports.length }} 份</span>
      </div>
      <div v-if="loading && !reports.length" class="alarm-empty-state" role="status">
        <span class="spinner dark" aria-hidden="true"></span><strong>正在读取实体告警报告</strong>
      </div>
      <div v-else-if="!reports.length" class="alarm-empty-state" role="status">
        <span class="empty-icon"><AppIcon name="file" size="24" /></span><strong>暂无实体告警报告</strong><span>发现新的告警问题后，系统将在这里生成对应的 PDF 报告。</span>
      </div>
      <div v-else class="alarm-table-wrap report-table-wrap">
        <table v-content-table aria-label="实体告警报告">
          <thead><tr><th>报告名称</th><th>关联事件</th><th>生成时间</th><th>文件大小</th><th>状态</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in reports" :key="item.report_id">
            <td><span class="report-name"><AppIcon name="file" size="16" tone="red" />{{ reportName(item) }}</span></td>
            <td>{{ item.incident_ids.length }} 项 · {{ item.machine_no || '--' }}</td>
            <td>{{ formatShortDateTime(item.created_at) }}</td>
            <td>{{ formatBytes(item.file_size) }}</td>
            <td><span class="report-status" :class="item.status">{{ reportStatusLabels[item.status] || item.status }}</span></td>
            <td class="report-actions"><template v-if="item.status === 'completed'"><a :href="item.download_url">下载</a><a :href="item.view_url" target="_blank" rel="noopener">预览</a></template><span v-else :title="item.error_message || ''">{{ item.error_message || '--' }}</span></td>
          </tr></tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.alarm-content-grid>.panel{margin-bottom:0}
.alarm-page{--alarm-navy:#153f5d;--alarm-blue:#237fb7;--alarm-teal:#16a39d;--alarm-green:#2aa473;--alarm-amber:#d99132;--alarm-red:#df5961;--alarm-indigo:#6674cf;--alarm-line:#dbe6ec;display:grid;gap:14px;padding-bottom:2px;color:var(--alarm-navy)}.alarm-page>.panel{margin-bottom:0}.alarm-hero{position:relative;min-height:165px;display:grid;grid-template-columns:minmax(420px,1fr) 230px 270px;align-items:center;gap:24px;overflow:hidden;padding:24px 28px;border:1px solid rgba(255,255,255,.72);border-radius:14px;background:linear-gradient(90deg,rgba(246,252,255,.98) 0%,rgba(238,248,253,.94) 43%,rgba(8,57,88,.06) 72%),var(--alarm-hero-image) center 46%/cover no-repeat;box-shadow:0 12px 30px rgba(22,62,89,.07);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.alarm-hero::after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(115deg,transparent 0 48%,rgba(255,255,255,.3) 48.2%,transparent 48.8%)}.alarm-hero__copy,.alarm-hero__slogan,.monitor-card{position:relative;z-index:1}.alarm-hero .eyebrow{margin:0;color:var(--alarm-blue);font-size:10px;font-weight:700;letter-spacing:1.4px}.alarm-hero h1{margin:6px 0;color:#123d5d;font-size:38px;line-height:1.1;letter-spacing:1px}.alarm-hero__copy>p:last-child{max-width:610px;margin:0;color:#607a8d;font-size:13px;line-height:1.7}.alarm-hero__slogan{align-self:center;margin:0;color:#edfaff;text-align:center;text-shadow:0 2px 10px rgba(4,37,58,.8);transform:rotate(-7deg)}.alarm-hero__slogan strong,.alarm-hero__slogan span{display:block}.alarm-hero__slogan strong{font-size:17px;letter-spacing:3px}.alarm-hero__slogan span{margin-top:6px;font-size:13px;letter-spacing:5px}.monitor-card{padding:15px 17px;border:1px solid rgba(80,144,170,.16);border-radius:11px;background:rgba(255,255,255,.9);box-shadow:0 12px 28px rgba(20,62,88,.12);transition:transform .2s ease,box-shadow .2s ease}.monitor-card:hover{transform:translateY(-2px);box-shadow:0 15px 32px rgba(20,62,88,.16)}.monitor-card__head{display:flex;align-items:center;gap:10px;padding-bottom:9px;border-bottom:1px solid #e2eaee}.monitor-card__head p{margin:0}.monitor-card__head small,.monitor-card__head strong{display:block}.monitor-card__head small{color:#72899a;font-size:10px}.monitor-card__head strong{margin-top:2px;color:#b4533a;font-size:17px}.monitor-card.ready .monitor-card__head strong{color:#0c8a69}.monitor-pulse{width:32px;height:32px;display:grid;place-items:center;color:var(--alarm-red);border-radius:50%;background:#fff0f1;box-shadow:0 0 0 5px rgba(223,89,97,.07)}.monitor-card.ready .monitor-pulse{background:#e9f8f2;box-shadow:0 0 0 5px rgba(42,164,115,.07)}.monitor-card dl{margin:6px 0 0}.monitor-card dl div{display:grid;grid-template-columns:55px 1fr;gap:6px;padding-top:6px;font-size:10px}.monitor-card dt{color:#7a8e9d}.monitor-card dd{min-width:0;margin:0;overflow:hidden;color:#2c526d;text-overflow:ellipsis;white-space:nowrap}.alarm-notice{display:flex;align-items:center;gap:9px;padding:11px 14px;border:1px solid #f1dcae;border-radius:8px;background:#fff8e9;color:#79580f;font-size:12px}.alarm-kpis{display:grid;grid-template-columns:.95fr .95fr 1fr 1.28fr 1fr;gap:10px;margin-bottom:0}.alarm-kpi{--kpi-color:var(--alarm-blue);--kpi-soft:#eaf5fb;position:relative;min-width:0;min-height:116px;display:flex;gap:12px;overflow:hidden;padding:15px 14px;border:1px solid var(--alarm-line);border-top:2px solid var(--kpi-color);border-radius:11px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.055);transition:transform .18s ease,box-shadow .18s ease;animation:surface-in .42s cubic-bezier(.2,.75,.25,1) both}.alarm-kpi::after{content:"";position:absolute;width:76px;height:76px;right:-30px;bottom:-42px;border-radius:50%;background:var(--kpi-soft);opacity:.78;transition:transform .24s ease}.alarm-kpi:hover{transform:translateY(-2px);box-shadow:0 11px 26px rgba(22,62,89,.09)}.alarm-kpi:hover::after{transform:scale(1.24)}.alarm-kpi:nth-child(2){animation-delay:.04s}.alarm-kpi:nth-child(3){animation-delay:.08s}.alarm-kpi:nth-child(4){animation-delay:.12s}.alarm-kpi:nth-child(5){animation-delay:.16s}.kpi-red{--kpi-color:var(--alarm-red);--kpi-soft:#fff0f1}.kpi-indigo{--kpi-color:var(--alarm-indigo);--kpi-soft:#eef0fb}.kpi-teal{--kpi-color:var(--alarm-teal);--kpi-soft:#e8f7f5}.kpi-green{--kpi-color:var(--alarm-green);--kpi-soft:#eaf7f1}.alarm-kpi__icon{position:relative;z-index:1;width:34px;height:34px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:50%;background:var(--kpi-soft);transition:transform .2s ease}.alarm-kpi:hover .alarm-kpi__icon{transform:scale(1.08) rotate(-3deg)}.alarm-kpi>div{min-width:0;flex:1}.alarm-kpi>div>span{display:block;color:#284a62;font-size:11px;font-weight:700;white-space:nowrap}.alarm-kpi>div>span small{display:block;margin-top:3px;color:#8294a1;font-size:8px;font-weight:500;letter-spacing:.6px}.alarm-kpi p{margin:10px 0 6px;color:#173f64;font-size:11px;white-space:nowrap}.alarm-kpi p strong{font-size:25px;font-variant-numeric:tabular-nums}.alarm-kpi--wide p strong{font-size:16px}.alarm-kpi em{position:relative;z-index:1;display:block;overflow:hidden;color:#788d9c;text-overflow:ellipsis;white-space:nowrap;font-style:normal;font-size:10px}.alarm-kpi.kpi-red em{color:var(--alarm-red)}.alarm-kpi.kpi-red em.safe,.alarm-kpi.kpi-green em,.alarm-kpi.kpi-teal em{color:var(--alarm-green)}.alarm-content-grid{display:grid;grid-template-columns:minmax(0,3.2fr) minmax(260px,1fr);gap:12px}.alarm-list,.alarm-insights{min-width:0;overflow:hidden;border:1px solid var(--alarm-line);border-radius:11px;background:#fff;box-shadow:0 7px 22px rgba(22,62,89,.05);animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both}.alarm-section-head,.alarm-insights>header{min-height:52px;display:flex;align-items:center;gap:16px;padding:7px 15px;border-bottom:1px solid #e5edf1;background:linear-gradient(90deg,#fff,#fbfdfe)}.alarm-section-title{display:flex;align-items:center;gap:8px;flex:0 0 auto}.alarm-section-title h2{margin:0;color:#193c57;font-size:14px}.alarm-section-title h2 small{margin-left:8px;color:#7b91a1;font-size:8px;font-weight:500;letter-spacing:.8px}.section-icon{width:28px;height:28px;display:grid;place-items:center;color:var(--alarm-teal);border-radius:8px;background:#e8f7f6}.section-icon.danger{color:var(--alarm-red);background:#fff0f1}.alarm-tabs{display:flex;align-items:center;gap:5px;margin-left:4px}.alarm-tabs button{min-height:31px;padding:0 14px;border:1px solid transparent;border-radius:7px;color:#617a8d;background:#f2f6f9;font-size:10px;transition:color .18s ease,background .18s ease,transform .18s ease}.alarm-tabs button:hover{transform:translateY(-1px);color:var(--alarm-blue);background:#eaf4fa}.alarm-tabs button.active{color:#fff;border-color:#1b83bf;background:linear-gradient(135deg,#1b83bf,#25a2cf);box-shadow:0 6px 14px rgba(31,137,185,.2)}.alarm-refresh-button{min-height:32px;display:inline-flex;align-items:center;gap:5px;margin-left:auto;padding:0 10px;color:var(--alarm-blue);border:1px solid #cde0ea;border-radius:7px;background:#f7fbfd;font-size:10px}.alarm-refresh-button:hover:not(:disabled){background:#eaf5fa}.alarm-empty-state{min-height:116px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;padding:26px;text-align:center;background:#fbfcfd}.alarm-empty-state strong{color:#385164;font-size:14px}.alarm-empty-state>span:not(.spinner):not(.empty-icon){color:#718697;font-size:11px;line-height:1.6}.empty-icon{width:38px;height:38px;display:grid;place-items:center;color:var(--alarm-green);border-radius:50%;background:#eaf7f1}.alarm-table-wrap{overflow-x:auto}.alarm-table-wrap table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}.alarm-table-wrap th,.alarm-table-wrap td{height:45px;padding:8px 10px;border-bottom:1px solid #e7eef2;text-align:left;vertical-align:middle;white-space:nowrap}.alarm-table-wrap th{color:#536f83;background:#f2f7fa;font-size:10px;font-weight:600;letter-spacing:.2px}.alarm-table-wrap td{font-size:13px}.alarm-table-wrap tbody tr{transition:background .15s ease}.alarm-table-wrap tbody tr:hover{background:#f7fbfd}.alarm-table-wrap td{color:#5d7588;font-size:10px}.alarm-table-wrap td small{display:block;max-width:160px;margin-top:3px;overflow:hidden;color:#8a9aa5;text-overflow:ellipsis}.alarm-name{display:block;max-width:150px;overflow:hidden;color:#345268;text-overflow:ellipsis;font-size:10px}.severity-badge,.incident-state,.report-status{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:6px;font-size:10px;font-weight:600}.severity-badge>span{width:16px;height:16px;display:grid;place-items:center;color:#fff;border-radius:50%;font-size:10px}.severity-badge.critical{color:#d94751;background:#fff0f1}.severity-badge.critical>span{background:#ef5b63}.severity-badge.high{color:#d58325;background:#fff5e7}.severity-badge.high>span{background:#e99a36}.severity-badge.warning{color:#2586be;background:#eaf5fb}.severity-badge.warning>span{background:#2c94ce}.live-state{display:inline-flex;align-items:center;gap:6px;color:#637c8e}.live-state i{width:8px;height:8px;border-radius:50%;background:#19a98d;box-shadow:0 0 0 3px rgba(25,169,141,.08)}.live-state.open i{background:#ef5b63;box-shadow:0 0 0 3px rgba(239,91,99,.08)}.incident-state.open{color:#db4b54;background:#fff0f1}.incident-state.closed,.report-status.completed{color:#168c67;background:#e8f7f1}.report-status.generating{color:#c47b22;background:#fff4e4}.report-status.failed{color:#d74850;background:#fff0f1}.cause-cell{max-width:150px;overflow:hidden;text-overflow:ellipsis}.report-actions{white-space:nowrap}.report-actions a{margin-right:12px;color:#1681bd;text-decoration:none;font-weight:600}.report-actions a:hover{text-decoration:underline}.report-actions>span{display:inline-block;max-width:120px;overflow:hidden;color:#8a9aa5;text-overflow:ellipsis;vertical-align:middle}.alarm-insights{padding-bottom:10px}.alarm-insights>header{justify-content:space-between}.alarm-insights>header>span{color:#2687b7;font-size:9px}.insight-card{display:flex;align-items:flex-start;gap:11px;margin:10px 12px 0;padding:14px;border-radius:8px;background:#eef7fb}.insight-card>span{width:30px;height:30px;display:grid;place-items:center;flex:0 0 auto;color:var(--alarm-blue)}.insight-card p{min-width:0;margin:0}.insight-card strong,.insight-card small{display:block}.insight-card strong{color:#2480b1;font-size:11px}.insight-card small{margin-top:5px;color:#61798c;font-size:10px;line-height:1.6}.insight-card--red{background:#fff3f3}.insight-card--red>span,.insight-card--red strong{color:var(--alarm-red)}.insight-card--amber{background:#fff8ef}.insight-card--amber>span,.insight-card--amber strong{color:var(--alarm-amber)}.report-head{justify-content:space-between}.report-count{color:#2687b7;font-size:10px}.report-table-wrap th,.report-table-wrap td{height:39px}.report-table-wrap th{background:#f2f7fa}.report-name{display:inline-flex;align-items:center;gap:8px;color:#3e5e73}.report-status{border-radius:999px}.global-error{border-radius:9px}
@media(max-width:1180px){.alarm-hero{grid-template-columns:minmax(380px,1fr) 170px 250px}.alarm-kpis{grid-template-columns:repeat(5,minmax(0,1fr))}.alarm-kpi{padding:14px 11px}.alarm-kpi__icon{width:30px;height:30px}.alarm-section-head{flex-wrap:wrap}.alarm-tabs{order:3;width:100%;margin:0}.alarm-refresh-button{margin-left:auto}.alarm-table-wrap table{min-width:960px}}
@media(max-width:900px){.alarm-page{gap:12px}.alarm-hero{grid-template-columns:1fr 260px;min-height:200px;padding:24px}.alarm-hero__slogan{display:none}.alarm-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.alarm-kpi--wide{grid-column:span 2}.alarm-content-grid{grid-template-columns:1fr}.alarm-insights{display:grid;grid-template-columns:repeat(3,1fr);padding-bottom:10px}.alarm-insights>header{grid-column:1/-1}.insight-card{margin:10px 0 0 10px}.insight-card:last-child{margin-right:10px}}
@media(max-width:620px){.alarm-hero{grid-template-columns:1fr;padding:22px 18px;background:linear-gradient(90deg,rgba(246,252,255,.96),rgba(238,248,253,.88)),var(--alarm-hero-image) center/cover}.alarm-hero h1{font-size:30px}.alarm-hero__copy>p:last-child{font-size:12px}.monitor-card{width:100%;box-sizing:border-box}.alarm-kpis{grid-template-columns:1fr}.alarm-kpi--wide{grid-column:auto}.alarm-section-head{position:relative;align-items:flex-start}.alarm-section-title{width:100%}.alarm-tabs{display:grid;grid-template-columns:repeat(2,1fr)}.alarm-tabs button{padding:0 8px}.alarm-refresh-button{position:absolute;right:14px}.alarm-insights{display:block}.insight-card{margin:10px 10px 0}.alarm-empty-state{min-height:104px;padding:22px 16px}}
@media(prefers-reduced-motion:reduce){.alarm-hero,.alarm-kpi,.alarm-list,.alarm-insights{animation:none}.alarm-kpi,.monitor-card,.alarm-tabs button{transition:none}}
</style>
