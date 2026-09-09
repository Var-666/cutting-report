<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getAlarmStatus, listAlarmIncidents, listAlarmReports } from '../api/reports'

const status = ref(null)
const incidents = ref([])
const reports = ref([])
const loading = ref(false)
const error = ref('')
let refreshTimer = null

const openCount = computed(() => incidents.value.filter((item) => item.status === 'open').length)
const completedCount = computed(() => reports.value.filter((item) => item.status === 'completed').length)
const severityLabels = { warning: '提示', high: '高', critical: '严重' }
const reportStatusLabels = { generating: '生成中', completed: '已生成', failed: '生成失败' }

function formatDateTime(value) {
  return value ? value.replace('T', ' ').slice(0, 19) : '--'
}

function formatBytes(value) {
  if (!value) return '--'
  return value >= 1024 * 1024 ? `${(value / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(value / 1024)} KB`
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [statusData, incidentData, reportData] = await Promise.all([
      getAlarmStatus(),
      listAlarmIncidents({ limit: 100 }),
      listAlarmReports({ limit: 50 }),
    ])
    status.value = statusData
    incidents.value = incidentData
    reports.value = reportData
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await loadData()
  refreshTimer = window.setInterval(loadData, 60_000)
})
onBeforeUnmount(() => window.clearInterval(refreshTimer))
</script>

<template>
  <div class="alarm-page">
    <section class="report-hero alarm-hero">
      <div>
        <p class="eyebrow">INTELLIGENT ALARM</p>
        <h2>智能告警中心</h2>
        <p>每 5 分钟检测 20kw切割机；仅首次出现的新问题生成实体 PDF。</p>
      </div>
      <div class="monitor-card" :class="{ ready: status?.enabled && status?.worker_running }">
        <span>监控状态</span>
        <strong>{{ status?.enabled ? (status.worker_running ? '运行中' : '等待启动') : '未启用' }}</strong>
        <small>设备：{{ status?.machine_nos?.join('、') || '未配置' }}</small>
        <small>最近同步：{{ formatDateTime(status?.last_success_at) }}</small>
      </div>
    </section>

    <div v-if="error" class="global-error"><strong>告警数据读取失败</strong><span>{{ error }}</span><button @click="loadData">重试</button></div>
    <div v-if="status && !status.enabled" class="alarm-notice">告警监控尚未启用。部署人员确认数据库与大模型配置后，将 <code>ALARM_MONITOR_ENABLED</code> 设为 <code>true</code>。</div>

    <section class="leadership-kpis alarm-kpis" aria-label="告警监控概况">
      <article><span>监控设备</span><strong>{{ status?.machine_nos?.length || 0 }}</strong><small>台</small></article>
      <article><span>当前未解除</span><strong>{{ openCount }}</strong><small>项</small></article>
      <article><span>实体 PDF</span><strong>{{ completedCount }}</strong><small>份</small></article>
      <article><span>数据游标</span><strong>{{ status?.last_alarm_id || 0 }}</strong><small>ID</small></article>
    </section>

    <section class="panel alarm-list">
      <div class="panel-title">
        <div><span>01</span><h2>告警事件</h2></div>
        <button class="alarm-refresh-button" type="button" :disabled="loading" :aria-busy="loading" @click="loadData">
          <span v-if="loading" class="spinner dark" aria-hidden="true"></span>{{ loading ? '刷新中' : '刷新' }}
        </button>
      </div>
      <div v-if="loading && !incidents.length" class="alarm-empty-state" role="status">
        <span class="spinner dark" aria-hidden="true"></span>
        <strong>正在读取告警事件</strong>
      </div>
      <div v-else-if="!incidents.length" class="alarm-empty-state" role="status">
        <strong>暂无告警事件</strong>
        <span>20kW 切割机当前无未解除告警，系统将持续监控。</span>
      </div>
      <div v-else class="alarm-table-wrap">
        <table v-content-table aria-label="20kw切割机告警事件">
          <thead><tr><th>状态</th><th>级别</th><th>编码 / 类别</th><th>告警描述</th><th>首次发现</th><th>最后检查</th><th>PDF</th></tr></thead>
          <tbody><tr v-for="item in incidents" :key="item.incident_id">
            <td><span class="incident-state" :class="item.status">{{ item.status === 'open' ? '未解除' : '已解除' }}</span></td>
            <td><strong :class="`severity-${item.severity}`">{{ severityLabels[item.severity] || item.severity }}</strong></td>
            <td>{{ item.alarm_code || '--' }}<small>{{ item.alarm_class || '--' }}</small></td>
            <td>{{ item.alarm_desc || '--' }}</td>
            <td>{{ formatDateTime(item.first_seen_at) }}</td><td>{{ formatDateTime(item.last_seen_at) }}</td>
            <td>{{ item.pdf_report_id ? '已生成' : '等待生成' }}</td>
          </tr></tbody>
        </table>
      </div>
    </section>

    <section class="panel alarm-list">
      <div class="panel-title"><div><span>02</span><h2>实体告警报告</h2></div><p>相同问题持续期间不会重复生成</p></div>
      <div v-if="loading && !reports.length" class="alarm-empty-state" role="status">
        <span class="spinner dark" aria-hidden="true"></span>
        <strong>正在读取实体告警报告</strong>
      </div>
      <div v-else-if="!reports.length" class="alarm-empty-state" role="status">
        <strong>暂无实体告警报告</strong>
        <span>发现新的告警问题后，系统将在这里生成对应的 PDF 报告。</span>
      </div>
      <div v-else class="alarm-table-wrap">
        <table v-content-table aria-label="实体告警报告">
          <thead><tr><th>生成时间</th><th>设备</th><th>问题数</th><th>模型状态</th><th>报告状态</th><th>大小</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in reports" :key="item.report_id">
            <td>{{ formatDateTime(item.created_at) }}<small>{{ item.report_id }}</small></td><td>{{ item.machine_no }}</td>
            <td>{{ item.incident_ids.length }}</td><td>{{ item.model_status || '--' }}</td><td>{{ reportStatusLabels[item.status] || item.status }}</td><td>{{ formatBytes(item.file_size) }}</td>
            <td class="report-actions"><template v-if="item.status === 'completed'"><a :href="item.view_url" target="_blank" rel="noopener">预览</a><a :href="item.download_url">下载 PDF</a></template><span v-else :title="item.error_message || ''">{{ item.error_message || '--' }}</span></td>
          </tr></tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.alarm-page{display:grid;gap:22px;padding-bottom:2px}.alarm-page>.panel{margin-bottom:0}.alarm-hero{min-height:175px}.alarm-hero h2{margin:7px 0 6px;font-size:32px}.alarm-hero p:not(.eyebrow){margin:0;color:var(--muted)}.monitor-card{width:310px;padding:17px 20px;border:1px solid rgba(13,111,158,.16);border-left:4px solid #c94a43;background:rgba(255,255,255,.74);box-shadow:0 12px 30px rgba(24,60,86,.07)}.monitor-card.ready{border-color:#b8d9c6;border-left-color:#2c9961;background:#f2faf5}.monitor-card span,.monitor-card small{display:block;color:var(--muted);font-size:11px}.monitor-card strong{display:block;margin:5px 0;color:var(--navy);font-size:22px}.monitor-card small+small{margin-top:4px}.alarm-notice{padding:12px 15px;border-left:4px solid #d29b3e;background:#fff8e9;color:#79580f}.alarm-kpis{margin-bottom:0}.alarm-list .panel-title p{margin:0}.alarm-refresh-button{min-height:34px;padding:0 13px;color:var(--blue);border-color:#a9c5d2;background:#f7fbfc;font-size:12px}.alarm-empty-state{min-height:116px;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;padding:26px;text-align:center;background:#fbfcfd}.alarm-empty-state strong{color:#385164;font-size:14px}.alarm-empty-state>span:not(.spinner){color:var(--muted);font-size:12px;line-height:1.6}.alarm-table-wrap{overflow-x:auto}.alarm-table-wrap table{width:100%;min-width:1050px;border-collapse:collapse;font-variant-numeric:tabular-nums}.alarm-table-wrap th,.alarm-table-wrap td{height:47px;padding:10px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}.alarm-table-wrap th{color:#fff;background:#0b4f76;font-size:11px;font-weight:600;letter-spacing:.4px}.alarm-table-wrap td{font-size:13px}.alarm-table-wrap tbody tr:hover{background:#f4f9fb}.alarm-table-wrap td small{display:block;margin-top:4px;color:var(--muted);font-size:11px}.incident-state{display:inline-block;padding:4px 8px;border-radius:999px;font-size:11px}.incident-state.open{color:#a52a24;background:#fff0ef}.incident-state.closed{color:#236a42;background:#eaf7ef}.severity-critical{color:#b91c1c}.severity-high{color:#c05621}.severity-warning{color:#9a6700}.report-actions{white-space:nowrap}.report-actions a{margin-right:12px;color:var(--blue);text-decoration:none}.report-actions span{display:block;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@media(max-width:900px){.alarm-hero{align-items:flex-start;flex-direction:column;justify-content:center;padding:28px 0}.monitor-card{width:100%}.alarm-kpis{grid-template-columns:1fr 1fr}}@media(max-width:560px){.alarm-page{gap:14px}.alarm-kpis{grid-template-columns:1fr}.alarm-empty-state{min-height:104px;padding:22px 16px}.alarm-refresh-button{min-height:34px;padding:0 10px}}
</style>
