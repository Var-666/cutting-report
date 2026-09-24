<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import {
  deleteAutomationSchedule,
  getAutomationConfiguration,
  getAutomationStatus,
  listAutomationSchedules,
  listEmailDeliveries,
  retryEmailDelivery,
  runAutomationSchedule,
  saveAutomationConfiguration,
  saveAutomationSchedule,
} from '../api/reports'
import { formatChineseDate, formatChineseDateTime } from '../utils/dateDisplay.js'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const status = ref(null)
const schedules = ref([])
const deliveries = ref([])
const deliveryPage = ref(1)
const deliveryTotal = ref(0)
const loading = ref(false)
const saving = ref(false)
const message = ref('')
const error = ref('')
const editingId = ref('')
const configurationLoading = ref(false)
const configurationLoaded = ref(false)
const configurationSaving = ref(false)
const configurationMessage = ref('')
const configurationError = ref('')
const editorSection = ref(null)
const hasUnsavedChanges = ref(false)
useUnsavedChanges(hasUnsavedChanges, '自动化配置还有未保存的修改，确定离开吗？')

const configuration = reactive({
  automation_enabled: true,
  automation_scan_seconds: 15,
  email_retry_base_seconds: 60,
  smtp_enabled: false,
  smtp_host: '',
  smtp_port: 587,
  smtp_username: '',
  smtp_password: '',
  smtp_password_configured: false,
  smtp_from_email: '',
  smtp_from_name: '切割生产智能产报系统',
  smtp_use_tls: true,
  smtp_use_ssl: false,
  smtp_timeout_seconds: 20,
})

const emptyForm = () => ({
  name: '每日车间报表',
  report_type: 'workshop',
  frequency: 'daily',
  run_time: '18:00',
  weekday: 1,
  month_day: 1,
  report_period: 'previous_day',
  recipients_text: '',
  subject_template: '{report_name} - {report_date}',
  chart_type: 'bar_line',
  attach_report: true,
  max_attempts: 3,
  enabled: true,
})
const form = reactive(emptyForm())

const pageCount = computed(() => Math.max(1, Math.ceil(deliveryTotal.value / 20)))
const reportLabels = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表' }
const deliveryLabels = {
  pending: '等待发送', sending: '发送中', retry_wait: '等待重试', sent: '已发送', failed: '发送失败',
}

watch(() => form.report_type, (type) => {
  form.report_period = type === 'leadership' ? 'previous_month' : 'previous_day'
})

function selectTls() {
  if (configuration.smtp_use_tls) configuration.smtp_use_ssl = false
}

function selectSsl() {
  if (configuration.smtp_use_ssl) configuration.smtp_use_tls = false
}

async function loadConfiguration() {
  configurationLoading.value = true
  configurationError.value = ''
  try {
    const data = await getAutomationConfiguration()
    Object.assign(configuration, data, { smtp_password: '' })
    configurationLoaded.value = true
    hasUnsavedChanges.value = false
  } catch (exception) {
    configurationError.value = exception.message
  } finally {
    configurationLoading.value = false
  }
}

async function submitConfiguration() {
  configurationSaving.value = true
  configurationMessage.value = ''
  configurationError.value = ''
  try {
    const result = await saveAutomationConfiguration({
      automation_enabled: configuration.automation_enabled,
      automation_scan_seconds: Number(configuration.automation_scan_seconds),
      email_retry_base_seconds: Number(configuration.email_retry_base_seconds),
      smtp_enabled: configuration.smtp_enabled,
      smtp_host: configuration.smtp_host,
      smtp_port: Number(configuration.smtp_port),
      smtp_username: configuration.smtp_username,
      smtp_password: configuration.smtp_password || null,
      smtp_from_email: configuration.smtp_from_email,
      smtp_from_name: configuration.smtp_from_name,
      smtp_use_tls: configuration.smtp_use_tls,
      smtp_use_ssl: configuration.smtp_use_ssl,
      smtp_timeout_seconds: Number(configuration.smtp_timeout_seconds),
    })
    if (configuration.smtp_password) configuration.smtp_password_configured = true
    configuration.smtp_password = ''
    configurationMessage.value = result.message
    hasUnsavedChanges.value = false
    await loadAll()
  } catch (exception) {
    configurationError.value = exception.message
  } finally {
    configurationSaving.value = false
  }
}

function resetForm() {
  editingId.value = ''
  Object.assign(form, emptyForm())
  hasUnsavedChanges.value = false
}

function editSchedule(item) {
  editingId.value = item.schedule_id
  Object.assign(form, {
    name: item.name,
    report_type: item.report_type,
    frequency: item.frequency,
    run_time: item.run_time,
    weekday: item.weekday || 1,
    month_day: item.month_day || 1,
    report_period: item.report_period,
    recipients_text: item.recipients.join('\n'),
    subject_template: item.subject_template,
    chart_type: item.chart_type,
    attach_report: item.attach_report,
    max_attempts: item.max_attempts,
    enabled: item.enabled,
  })
  nextTick(() => editorSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}

function payload() {
  return {
    name: form.name,
    report_type: form.report_type,
    frequency: form.frequency,
    run_time: form.run_time,
    weekday: form.frequency === 'weekly' ? Number(form.weekday) : null,
    month_day: form.frequency === 'monthly' ? Number(form.month_day) : null,
    report_period: form.report_period,
    recipients: form.recipients_text.split(/[\n,;]+/).map((item) => item.trim()).filter(Boolean),
    subject_template: form.subject_template,
    chart_type: form.chart_type,
    attach_report: form.attach_report,
    max_attempts: Number(form.max_attempts),
    enabled: form.enabled,
  }
}

async function loadAll() {
  loading.value = true
  error.value = ''
  try {
    const [statusData, scheduleData, deliveryData] = await Promise.all([
      getAutomationStatus(),
      listAutomationSchedules(),
      listEmailDeliveries({ page: deliveryPage.value, pageSize: 20 }),
    ])
    status.value = statusData
    schedules.value = scheduleData.items
    deliveries.value = deliveryData.items
    deliveryTotal.value = deliveryData.total
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

async function submit() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    await saveAutomationSchedule(payload(), editingId.value)
    message.value = editingId.value ? '定时任务已更新' : '定时任务已创建'
    resetForm()
    await loadAll()
  } catch (exception) {
    error.value = exception.message
  } finally {
    saving.value = false
  }
}

async function runNow(item) {
  error.value = ''
  message.value = ''
  try {
    const run = await runAutomationSchedule(item.schedule_id)
    if (run.status === 'failed') throw new Error(run.last_error || '报表任务创建失败')
    message.value = `已提交“${item.name}”，报表生成后将自动发送。`
    await loadAll()
  } catch (exception) {
    error.value = exception.message
  }
}

async function remove(item) {
  if (!window.confirm(`确定删除定时任务“${item.name}”？历史发送记录会保留。`)) return
  try {
    await deleteAutomationSchedule(item.schedule_id)
    if (editingId.value === item.schedule_id) resetForm()
    message.value = '定时任务已删除'
    await loadAll()
  } catch (exception) {
    error.value = exception.message
  }
}

async function retry(item) {
  try {
    await retryEmailDelivery(item.delivery_id)
    message.value = '邮件已重新加入发送队列'
    await loadAll()
  } catch (exception) {
    error.value = exception.message
  }
}

function scheduleText(item) {
  if (item.frequency === 'weekly') return `每周${'一二三四五六日'[item.weekday - 1]} ${item.run_time}`
  if (item.frequency === 'monthly') return `每月 ${item.month_day} 日 ${item.run_time}`
  return `每日 ${item.run_time}`
}

function formatDateTime(value) {
  return formatChineseDateTime(value)
}

onMounted(() => Promise.all([loadConfiguration(), loadAll()]))
</script>

<template>
  <div class="automation-page management-page">
    <ManagementPageHero
      eyebrow="REPORT AUTOMATION"
      title="报表自动化"
      description="定时生成报表，通过邮件队列发送，失败后按退避策略重试。"
      icon="activity"
      status-label="SMTP 服务"
      :status-value="status?.smtp?.configured ? '邮件通道已就绪' : '等待配置'"
      :status-note="status?.smtp?.configured ? `${status.smtp.host}:${status.smtp.port}` : '计划可先保存，发送失败会完整留痕'"
      :tone="status?.smtp?.configured ? 'green' : 'amber'"
    />

    <div v-if="message" class="automation-message success" role="status" aria-live="polite">{{ message }}</div>
    <div v-if="error" class="automation-message error" role="alert">{{ error }}，请检查配置或稍后重试。</div>

    <section class="automation-kpis">
      <article><span>已启用计划</span><strong>{{ status?.summary?.schedule_enabled || 0 }}</strong></article>
      <article><span>等待/重试</span><strong>{{ status?.summary?.delivery_pending || 0 }}</strong></article>
      <article><span>已发送</span><strong>{{ status?.summary?.delivery_sent || 0 }}</strong></article>
      <article><span>最终失败</span><strong>{{ status?.summary?.delivery_failed || 0 }}</strong></article>
    </section>

    <section class="panel automation-settings">
      <div class="panel-title">
        <div><span>01</span><h2>运行与邮件参数</h2></div>
        <p>在线保存至 data/settings.env，保存后立即生效</p>
      </div>
      <div v-if="configurationLoading" class="empty">正在读取自动化配置…</div>
      <div v-else-if="!configurationLoaded" class="configuration-load-error" role="alert">
        <span>{{ configurationError || '自动化配置读取失败' }}</span>
        <button type="button" @click="loadConfiguration">重新读取</button>
      </div>
      <form v-else class="configuration-form" @submit.prevent="submitConfiguration" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
        <div class="configuration-notice">
          <div>
            <strong>保存后，新任务与新邮件发送使用最新配置</strong>
            <span>正在执行的任务会按开始时的配置完成；无需重启服务。</span>
          </div>
          <span class="restart-badge">即时生效</span>
        </div>

        <div class="configuration-grid">
          <fieldset class="configuration-card">
            <legend>任务引擎</legend>
            <p class="card-description">控制自动化总开关、扫描频率和失败重试节奏。</p>
            <label class="setting-switch">
              <span><strong>启用报表自动化</strong><small>关闭后保留已有计划，但不再自动执行</small></span>
              <input v-model="configuration.automation_enabled" name="automation_enabled" type="checkbox">
            </label>
            <div class="setting-fields two-columns">
              <label><span>计划扫描间隔（秒）</span><input v-model.number="configuration.automation_scan_seconds" name="automation_scan_seconds" type="number" inputmode="numeric" autocomplete="off" min="1" max="3600" step="1" required><small>建议 15 秒，范围 1–3600 秒</small></label>
              <label><span>重试基准间隔（秒）</span><input v-model.number="configuration.email_retry_base_seconds" name="email_retry_base_seconds" type="number" inputmode="numeric" autocomplete="off" min="1" max="86400" step="1" required><small>后续重试会按退避策略延长</small></label>
            </div>
          </fieldset>

          <fieldset class="configuration-card smtp-configuration-card">
            <legend>SMTP 邮件服务</legend>
            <p class="card-description">配置发件服务器及安全连接方式，密码不会在页面回显。</p>
            <label class="setting-switch">
              <span><strong>启用 SMTP 发送</strong><small>关闭时仍可编辑并保存下方参数</small></span>
              <input v-model="configuration.smtp_enabled" name="smtp_enabled" type="checkbox">
            </label>
            <div class="setting-fields smtp-fields">
              <label class="span-two"><span>SMTP 服务器</span><input v-model.trim="configuration.smtp_host" name="smtp_host" autocomplete="off" spellcheck="false" :required="configuration.smtp_enabled" maxlength="255" placeholder="例如：smtp.example.com…"></label>
              <label><span>端口</span><input v-model.number="configuration.smtp_port" name="smtp_port" type="number" inputmode="numeric" autocomplete="off" min="1" max="65535" required></label>
              <label><span>超时（秒）</span><input v-model.number="configuration.smtp_timeout_seconds" name="smtp_timeout_seconds" type="number" inputmode="numeric" autocomplete="off" min="1" max="300" step="1" required></label>
              <label><span>登录账号</span><input v-model.trim="configuration.smtp_username" name="smtp_username" maxlength="255" autocomplete="username" spellcheck="false" placeholder="可留空…"></label>
              <label><span>登录密码</span><input v-model="configuration.smtp_password" name="smtp_password" type="password" maxlength="256" autocomplete="new-password" :placeholder="configuration.smtp_password_configured ? '已配置，留空则保留原密码…' : '请输入邮箱密码…'"></label>
              <label><span>发件人邮箱</span><input v-model.trim="configuration.smtp_from_email" name="smtp_from_email" type="email" inputmode="email" spellcheck="false" autocomplete="off" :required="configuration.smtp_enabled" maxlength="320" placeholder="例如：reports@example.com…"></label>
              <label><span>发件人名称</span><input v-model.trim="configuration.smtp_from_name" name="smtp_from_name" autocomplete="off" maxlength="100" placeholder="例如：切割生产智能产报系统…"></label>
            </div>
            <div class="security-options">
              <span>连接安全</span>
              <label><input v-model="configuration.smtp_use_tls" name="smtp_use_tls" type="checkbox" @change="selectTls">STARTTLS</label>
              <label><input v-model="configuration.smtp_use_ssl" name="smtp_use_ssl" type="checkbox" @change="selectSsl">SSL/TLS</label>
              <small>两种安全方式只能选择一种，也可都不选。</small>
            </div>
          </fieldset>
        </div>

        <div v-if="configurationMessage" class="configuration-feedback success" role="status" aria-live="polite">{{ configurationMessage }}</div>
        <div v-if="configurationError" class="configuration-feedback error" role="alert">{{ configurationError }}，请检查参数后重试。</div>
        <div class="configuration-actions">
          <span>密码仅用于服务端发信，不会通过配置查询接口返回。</span>
          <button type="submit" :disabled="configurationSaving">{{ configurationSaving ? '保存中…' : '保存自动化参数' }}</button>
        </div>
      </form>
    </section>

    <section ref="editorSection" class="panel automation-editor">
      <div class="panel-title">
        <div><span>02</span><h2>{{ editingId ? '编辑定时任务' : '新建定时任务' }}</h2></div>
        <p>邮件发送独立重试，不会重复生成报表</p>
      </div>
      <form class="automation-form" @submit.prevent="submit" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
        <label><span>任务名称</span><input v-model.trim="form.name" name="schedule_name" autocomplete="off" required maxlength="80"></label>
        <label><span>报表类型</span><AppSelect v-model="form.report_type" name="report_type" @change="hasUnsavedChanges = true"><option v-for="(label,key) in reportLabels" :key="key" :value="key">{{ label }}</option></AppSelect></label>
        <label><span>执行频率</span><AppSelect v-model="form.frequency" name="frequency" @change="hasUnsavedChanges = true"><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></AppSelect></label>
        <label><span>执行时间</span><input v-model="form.run_time" name="run_time" type="time" autocomplete="off" required></label>
        <label v-if="form.frequency === 'weekly'"><span>星期</span><AppSelect v-model.number="form.weekday" name="weekday" @change="hasUnsavedChanges = true"><option v-for="(day,index) in ['一','二','三','四','五','六','日']" :key="day" :value="index + 1">星期{{ day }}</option></AppSelect></label>
        <label v-if="form.frequency === 'monthly'"><span>每月执行日</span><input v-model.number="form.month_day" name="month_day" type="number" inputmode="numeric" autocomplete="off" min="1" max="28"></label>
        <label><span>报表期间</span><AppSelect v-model="form.report_period" name="report_period" @change="hasUnsavedChanges = true"><template v-if="form.report_type === 'leadership'"><option value="previous_month">上月</option><option value="current_month">本月</option></template><template v-else><option value="previous_day">前一日</option><option value="current_day">当日</option></template></AppSelect></label>
        <label><span>图表类型</span><AppSelect v-model="form.chart_type" name="chart_type" @change="hasUnsavedChanges = true"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></AppSelect></label>
        <label><span>最大尝试次数</span><input v-model.number="form.max_attempts" name="max_attempts" type="number" inputmode="numeric" autocomplete="off" min="1" max="10"></label>
        <label class="wide"><span>收件人（每行一个，或用逗号分隔）</span><textarea v-model="form.recipients_text" name="recipients" inputmode="email" autocomplete="off" spellcheck="false" rows="3" required placeholder="例如：manager@example.com…"></textarea></label>
        <label class="wide"><span>邮件主题</span><input v-model.trim="form.subject_template" name="subject_template" autocomplete="off" required><small>可用：{report_name}、{report_type}、{report_date}</small></label>
        <div class="automation-switches wide">
          <label><input v-model="form.attach_report" name="attach_report" type="checkbox">附加 HTML 报表</label>
          <label><input v-model="form.enabled" name="schedule_enabled" type="checkbox">启用定时任务</label>
        </div>
        <div class="automation-actions wide">
          <button v-if="editingId" type="button" class="secondary" @click="resetForm">取消编辑</button>
          <button type="submit" :disabled="saving">{{ saving ? '保存中…' : editingId ? '保存修改' : '创建任务' }}</button>
        </div>
      </form>
    </section>

    <section class="panel automation-list">
      <div class="panel-title"><div><span>03</span><h2>定时任务</h2></div><p>{{ schedules.length }} 个计划</p></div>
      <div v-if="loading && !schedules.length" class="empty">正在读取定时任务…</div>
      <div v-else-if="!schedules.length" class="empty">尚未创建定时任务。</div>
      <div v-else class="automation-table-wrap">
        <table v-content-table><thead><tr><th>状态</th><th>任务</th><th>报表</th><th>计划</th><th>下次执行</th><th>收件人</th><th>发送/失败</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in schedules" :key="item.schedule_id">
            <td><span class="status-dot" :class="{ enabled: item.enabled }"></span>{{ item.enabled ? '启用' : '停用' }}</td>
            <td><strong>{{ item.name }}</strong><small>{{ item.last_status || '尚未执行' }}</small></td>
            <td>{{ reportLabels[item.report_type] }}</td><td>{{ scheduleText(item) }}</td><td>{{ formatDateTime(item.next_run_at) }}</td>
            <td :title="item.recipients.join(', ')">{{ item.recipients.length }} 人</td><td>{{ item.sent_count || 0 }} / {{ item.failed_count || 0 }}</td>
            <td class="row-actions"><button type="button" @click="runNow(item)">立即执行</button><button type="button" @click="editSchedule(item)">编辑</button><button type="button" class="danger" @click="remove(item)">删除</button></td>
          </tr></tbody></table>
      </div>
    </section>

    <section class="panel automation-list">
      <div class="panel-title"><div><span>04</span><h2>邮件发送记录</h2></div><p>可查看尝试次数、失败原因并手动重发</p></div>
      <div v-if="!deliveries.length" class="empty">尚无邮件发送记录。</div>
      <div v-else class="automation-table-wrap"><table v-content-table><thead><tr><th>创建时间</th><th>任务</th><th>报表日期</th><th>收件人</th><th>状态</th><th>尝试</th><th>结果</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in deliveries" :key="item.delivery_id"><td>{{ formatDateTime(item.created_at) }}</td><td><strong>{{ item.schedule_name }}</strong><small>{{ item.subject }}</small></td><td>{{ formatChineseDate(item.report_date) }}</td><td>{{ item.recipients.join(', ') }}</td><td><span class="delivery-status" :class="item.status">{{ deliveryLabels[item.status] || item.status }}</span></td><td>{{ item.attempt_count }}/{{ item.max_attempts }}</td><td class="error-cell" :title="item.last_error || ''">{{ item.sent_at ? formatDateTime(item.sent_at) : item.last_error || '--' }}</td><td><button v-if="item.status === 'failed'" type="button" @click="retry(item)">重新发送</button><span v-else>--</span></td></tr></tbody></table></div>
      <div class="automation-pagination"><span>第 {{ deliveryPage }} / {{ pageCount }} 页，共 {{ deliveryTotal }} 条</span><div><button type="button" :disabled="deliveryPage <= 1" @click="deliveryPage--; loadAll()">上一页</button><button type="button" :disabled="deliveryPage >= pageCount" @click="deliveryPage++; loadAll()">下一页</button></div></div>
    </section>
  </div>
</template>

<style scoped>
.automation-page{display:grid;gap:18px}.smtp-card{width:300px;padding:18px 20px;border:1px solid #e0c38d;border-left:5px solid #d89a27;background:#fff9ec}.smtp-card.ready{border-color:#9acdb2;border-left-color:#2c9961;background:#f0faf4}.smtp-card span,.smtp-card small{display:block;color:var(--muted);font-size:11px}.smtp-card strong{display:block;margin:5px 0;color:var(--navy);font-size:22px}.automation-message{padding:12px 15px;border-left:4px solid}.automation-message.success{color:#236a42;border-color:#2c9961;background:#eaf7ef}.automation-message.error{color:#9f302a;border-color:#c94a43;background:#fff0ef}.automation-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.automation-kpis article{padding:19px 22px;border-top:4px solid var(--cyan);background:#fff;box-shadow:var(--shadow)}.automation-kpis span{display:block;color:var(--muted);font-size:11px}.automation-kpis strong{display:block;margin-top:7px;color:var(--blue);font-size:28px}.automation-form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;padding:24px}.automation-form label>span{display:block;margin-bottom:6px;color:#425767;font-size:11px;font-weight:700}.automation-form textarea{width:100%;resize:vertical;padding:10px 12px;border:1px solid #bdcbd5;background:#fbfcfd}.automation-form small{display:block;margin-top:5px;color:var(--muted);font-size:10px}.automation-form .wide{grid-column:1/-1}.automation-switches{display:flex;gap:18px}.automation-switches label{display:flex;align-items:center;gap:7px;color:#425767;font-size:11px}.automation-switches input{width:16px;height:16px}.automation-actions{display:flex;justify-content:flex-end;gap:9px;padding-top:8px;border-top:1px solid var(--line)}.automation-actions .secondary{color:var(--blue);background:#fff}.automation-table-wrap{overflow-x:auto}.automation-table-wrap table{width:100%;border-collapse:collapse}.automation-table-wrap th,.automation-table-wrap td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;font-size:11px}.automation-table-wrap th{color:#fff;background:var(--navy)}.automation-table-wrap td strong,.automation-table-wrap td small{display:block}.automation-table-wrap td small{margin-top:3px;max-width:240px;overflow:hidden;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}.status-dot{width:8px;height:8px;display:inline-block;margin-right:6px;border-radius:50%;background:#aab7bf}.status-dot.enabled{background:#2c9961}.row-actions{display:flex;gap:5px;white-space:nowrap}.row-actions button,.automation-table-wrap td>button{min-height:29px;padding:0 8px;font-size:10px}.row-actions .danger{color:#a33a32;border-color:#dfb5b1;background:#fff7f6}.delivery-status{display:inline-block;padding:4px 7px;border-radius:999px;background:#eef2f4}.delivery-status.sent{color:#1f7148;background:#e9f7ef}.delivery-status.failed{color:#9f302a;background:#fff0ef}.delivery-status.retry_wait{color:#8b5a08;background:#fff5dc}.error-cell{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.automation-pagination{display:flex;align-items:center;justify-content:space-between;padding:13px 16px;color:var(--muted);font-size:11px}.automation-pagination div{display:flex;gap:7px}.automation-pagination button{min-height:31px;padding:0 10px}@media(max-width:900px){.smtp-card{width:100%}.automation-kpis{grid-template-columns:1fr 1fr}.automation-form{grid-template-columns:1fr 1fr}}@media(max-width:560px){.automation-kpis,.automation-form{grid-template-columns:1fr}.automation-form .wide{grid-column:auto}.automation-switches{align-items:flex-start;flex-direction:column}.automation-actions{align-items:stretch;flex-direction:column}}
.automation-settings{overflow:hidden}.configuration-load-error{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:20px 24px;padding:14px 16px;border-left:4px solid #c94a43;color:#9f302a;background:#fff0ef;font-size:11px}.configuration-form{padding:24px;background:linear-gradient(180deg,#fff 0,#f8fbfc 100%)}.configuration-notice{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:18px;padding:14px 16px;border:1px solid #d7e4ea;border-left:4px solid var(--cyan);background:#f2f8fa}.configuration-notice strong,.configuration-notice span{display:block}.configuration-notice strong{color:var(--navy);font-size:12px}.configuration-notice div>span{margin-top:4px;color:var(--muted);font-size:10px;line-height:1.6}.restart-badge{flex:0 0 auto;padding:6px 10px;border-radius:999px;color:#895c0a;background:#fff1cc;font-size:10px;font-weight:700}.configuration-grid{display:grid;grid-template-columns:minmax(300px,.8fr) minmax(520px,1.4fr);gap:16px}.configuration-card{min-width:0;margin:0;padding:20px;border:1px solid #d9e3e9;border-top:4px solid var(--blue);background:#fff}.configuration-card legend{padding:0 9px;color:var(--navy);font-size:14px;font-weight:800}.configuration-card.smtp-configuration-card{border-top-color:var(--cyan)}.card-description{min-height:34px;margin:0 0 14px;color:var(--muted);font-size:10px;line-height:1.6}.setting-switch{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px;padding:13px 14px;background:#eef6f8}.setting-switch span,.setting-switch small{display:block}.setting-switch strong{color:#244b61;font-size:11px}.setting-switch small{margin-top:4px;color:var(--muted);font-size:9px;line-height:1.45}.setting-switch>input{width:18px;height:18px;flex:0 0 auto;accent-color:var(--cyan)}.setting-fields{display:grid;gap:13px}.setting-fields.two-columns,.setting-fields.smtp-fields{grid-template-columns:repeat(2,minmax(0,1fr))}.setting-fields .span-two{grid-column:1/-1}.setting-fields label>span{display:block;margin-bottom:6px;color:#425767;font-size:10px;font-weight:700}.setting-fields input{width:100%;min-height:40px;padding:0 11px;border:1px solid #bdcbd5;background:#fbfcfd;color:var(--ink)}.setting-fields input:focus-visible{outline:3px solid rgba(24,147,166,.18);outline-offset:1px;border-color:var(--cyan)}.setting-fields small{display:block;margin-top:5px;color:var(--muted);font-size:9px}.security-options{display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;margin-top:16px;padding-top:14px;border-top:1px solid var(--line);color:#425767;font-size:10px}.security-options>span{font-weight:700}.security-options label{display:flex;align-items:center;gap:5px}.security-options input{width:15px;height:15px;accent-color:var(--cyan)}.security-options small{flex:1 1 100%;color:var(--muted)}.configuration-feedback{margin-top:16px;padding:11px 13px;border-left:4px solid;font-size:11px}.configuration-feedback.success{color:#236a42;border-color:#2c9961;background:#eaf7ef}.configuration-feedback.error{color:#9f302a;border-color:#c94a43;background:#fff0ef}.configuration-actions{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}.configuration-actions span{color:var(--muted);font-size:10px}.configuration-actions button{min-width:176px}.automation-editor,.automation-list{overflow:hidden}.automation-form{background:linear-gradient(180deg,#fff 0,#fbfcfd 100%)}.automation-table-wrap tbody tr{transition:background-color .15s ease}.automation-table-wrap tbody tr:hover{background:#f3f8fa}.automation-table-wrap th{white-space:nowrap}
@media(max-width:1080px){.configuration-grid{grid-template-columns:1fr}.card-description{min-height:0}}
@media(max-width:560px){.configuration-form{padding:16px}.configuration-notice,.configuration-actions{align-items:stretch;flex-direction:column}.restart-badge{align-self:flex-start}.setting-fields.two-columns,.setting-fields.smtp-fields{grid-template-columns:1fr}.setting-fields .span-two{grid-column:auto}.configuration-actions button{width:100%}}
</style>
