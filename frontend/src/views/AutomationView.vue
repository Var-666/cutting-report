<script setup lang="ts">
import type { AutomationSchedule, AutomationStatus, EmailDelivery } from '../types/automation'

import { toError } from '../utils/errors'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { deleteAutomationSchedule, getAutomationConfiguration, getAutomationStatus, listAutomationSchedules, listEmailDeliveries, retryEmailDelivery, runAutomationSchedule, saveAutomationConfiguration, saveAutomationSchedule } from '../api/automation.ts'
import { deviceSelection } from '../state/devices.ts'
import { formatDeviceDisplay } from '../utils/deviceDisplay.ts'
import { formatChineseDate, formatChineseDateTime } from '../utils/dateDisplay.ts'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import AppIcon from '../components/AppIcon.vue'
import EmailDeliveryResolution from '../components/EmailDeliveryResolution.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.ts'

const status = ref<AutomationStatus | null>(null)
const schedules = ref<AutomationSchedule[]>([])
const deliveries = ref<EmailDelivery[]>([])
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
const editorSection = ref<HTMLElement | null>(null)
const hasUnsavedChanges = ref(false)
const selectedDelivery = ref<EmailDelivery | null>(null)
const resolutionDirty = ref(false)
useUnsavedChanges(computed(() => hasUnsavedChanges.value || resolutionDirty.value), '自动化配置还有未保存的修改，确定离开吗？')

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
  device_id: deviceSelection.id,
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
watch(() => deviceSelection.id, (id) => { if (!form.device_id) form.device_id = id })

const pageCount = computed(() => Math.max(1, Math.ceil(deliveryTotal.value / 20)))
const reportLabels: Record<string, string> = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表' }
const deliveryLabels: Record<string, string> = {
  pending: '等待发送', sending: '发送中', retry_wait: '等待重试', sent: '已发送', failed: '发送失败', unknown: '结果待核对',
}
const scheduleLabels: Record<string, string> = { ...deliveryLabels, queued: '等待生成报表' }

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
  } catch (caught) {
    const exception = toError(caught)
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
  } catch (caught) {
    const exception = toError(caught)
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

function editSchedule(item: AutomationSchedule) {
  editingId.value = item.schedule_id
  Object.assign(form, {
    device_id: item.device_id,
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
    device_id: form.device_id,
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
  } catch (caught) {
    const exception = toError(caught)
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
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  } finally {
    saving.value = false
  }
}

async function runNow(item: AutomationSchedule) {
  error.value = ''
  message.value = ''
  try {
    const run = await runAutomationSchedule(item.schedule_id)
    if (run.status === 'failed') throw new Error(run.last_error || '报表任务创建失败')
    message.value = `已提交“${item.name}”，报表生成后将自动发送。`
    await loadAll()
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  }
}

async function remove(item: AutomationSchedule) {
  if (!window.confirm(`确定删除定时任务“${item.name}”？历史发送记录会保留。`)) return
  try {
    await deleteAutomationSchedule(item.schedule_id)
    if (editingId.value === item.schedule_id) resetForm()
    message.value = '定时任务已删除'
    await loadAll()
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  }
}

async function retry(item: EmailDelivery) {
  try {
    await retryEmailDelivery(item.delivery_id)
    message.value = '邮件已重新加入发送队列'
    await loadAll()
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  }
}

function openDelivery(item: EmailDelivery) {
  if (resolutionDirty.value && !window.confirm('核对内容尚未保存，确定切换记录吗？')) return
  resolutionDirty.value = false
  selectedDelivery.value = item
}
async function deliveryResolved() {
  resolutionDirty.value = false
  message.value = '核对结果已保存；未接收的邮件仍需单独手动重试。'
  await loadAll()
  selectedDelivery.value = deliveries.value.find((item) => item.delivery_id === selectedDelivery.value?.delivery_id) || selectedDelivery.value
}

function scheduleText(item: AutomationSchedule) {
  if (item.frequency === 'weekly') return `每周${'一二三四五六日'[(item.weekday || 1) - 1]} ${item.run_time}`
  if (item.frequency === 'monthly') return `每月 ${item.month_day} 日 ${item.run_time}`
  return `每日 ${item.run_time}`
}

function formatDateTime(value: unknown) {
  return formatChineseDateTime(value)
}

onMounted(() => Promise.all([loadConfiguration(), loadAll()]))
</script>

<template>
  <div class="automation-page management-page">
    <ManagementPageHero
      eyebrow="REPORT AUTOMATION"
      title="报表自动化"
      description="按计划生成生产报表，自动发送邮件，并追踪每次发送结果。"
      icon="activity"
      status-label="SMTP 服务"
      :status-value="status ? status.smtp?.configured ? '邮件参数已配置' : '等待配置' : loading ? '正在读取' : '状态待检查'"
      :status-note="status?.smtp?.configured ? `${status.smtp.host}:${status.smtp.port}` : '配置发件服务器后即可发送报表'"
      :tone="status?.smtp?.configured ? 'green' : loading ? 'blue' : 'amber'"
    />

    <div v-if="message" class="automation-message success" role="status" aria-live="polite">{{ message }}</div>
    <div v-if="error" class="automation-message error" role="alert">{{ error }}，请检查配置或稍后重试。</div>

    <section class="automation-kpis" aria-label="报表自动化概况" :aria-busy="loading">
      <article><span class="kpi-icon"><AppIcon name="calendar" tone="muted" :size="20" /></span><div><span>已启用计划</span><strong>{{ status ? status.summary?.schedule_enabled || 0 : '—' }}</strong></div></article>
      <article><span class="kpi-icon"><AppIcon name="clock" tone="muted" :size="20" /></span><div><span>等待 / 重试</span><strong>{{ status ? status.summary?.delivery_pending || 0 : '—' }}</strong></div></article>
      <article><span class="kpi-icon"><AppIcon name="send" tone="muted" :size="20" /></span><div><span>已发送</span><strong>{{ status ? status.summary?.delivery_sent || 0 : '—' }}</strong></div></article>
      <article><span class="kpi-icon"><AppIcon name="alert" tone="muted" :size="20" /></span><div><span>失败 / 待核对</span><strong>{{ status ? status.summary?.delivery_failed || 0 : '—' }} <em>/</em> {{ status ? status.summary?.delivery_unknown || 0 : '—' }}</strong></div></article>
    </section>

    <div class="automation-workspace">
      <section class="panel automation-settings" aria-labelledby="automation-settings-title">
        <div class="panel-title">
          <div><span><AppIcon name="settings" tone="muted" :size="17" /></span><h2 id="automation-settings-title">运行与邮件参数</h2></div>
          <p>保存后即时生效</p>
        </div>
        <div v-if="configurationLoading" class="empty" role="status">正在读取自动化配置…</div>
        <div v-else-if="!configurationLoaded" class="configuration-load-error" role="alert">
          <span>{{ configurationError || '自动化配置读取失败' }}</span>
          <button type="button" @click="loadConfiguration">重新读取</button>
        </div>
        <form v-else class="configuration-form" @submit.prevent="submitConfiguration" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
          <div class="configuration-notice">
            <AppIcon name="check" tone="muted" :size="18" />
            <span>保存后用于新任务与新邮件，正在执行的任务不受影响。</span>
          </div>

          <div class="configuration-grid">
            <fieldset class="configuration-card">
              <legend>任务引擎</legend>
              <label class="setting-switch">
                <span><strong>启用报表自动化</strong><small>关闭后保留计划，暂停自动执行</small></span>
                <input v-model="configuration.automation_enabled" name="automation_enabled" type="checkbox">
              </label>
              <div class="setting-fields two-columns">
                <label><span>计划扫描间隔（秒）</span><input v-model.number="configuration.automation_scan_seconds" name="automation_scan_seconds" type="number" inputmode="numeric" autocomplete="off" min="1" max="3600" step="1" required><small>建议 15 秒，范围 1–3600 秒</small></label>
                <label><span>重试基准间隔（秒）</span><input v-model.number="configuration.email_retry_base_seconds" name="email_retry_base_seconds" type="number" inputmode="numeric" autocomplete="off" min="1" max="86400" step="1" required><small>后续重试间隔逐次延长</small></label>
              </div>
            </fieldset>

            <fieldset class="configuration-card smtp-configuration-card">
              <legend>SMTP 邮件服务</legend>
              <label class="setting-switch">
                <span><strong>启用 SMTP 发送</strong><small>关闭时仍可编辑并保存参数</small></span>
                <input v-model="configuration.smtp_enabled" name="smtp_enabled" type="checkbox">
              </label>
              <div class="setting-fields smtp-fields">
                <label class="span-two"><span>SMTP 服务器</span><input v-model.trim="configuration.smtp_host" name="smtp_host" autocomplete="off" spellcheck="false" :required="configuration.smtp_enabled" maxlength="255" placeholder="例如：mail.example.com" aria-describedby="smtp-host-hint"><small id="smtp-host-hint">填写服务器域名或 IP，不填 https:// 或网页路径。</small></label>
                <label><span>端口</span><input v-model.number="configuration.smtp_port" name="smtp_port" type="number" inputmode="numeric" autocomplete="off" min="1" max="65535" required></label>
                <label><span>超时（秒）</span><input v-model.number="configuration.smtp_timeout_seconds" name="smtp_timeout_seconds" type="number" inputmode="numeric" autocomplete="off" min="1" max="300" step="1" required></label>
                <label><span>登录账号</span><input v-model.trim="configuration.smtp_username" name="smtp_username" maxlength="255" autocomplete="username" spellcheck="false" placeholder="填写邮箱账号，免认证时可留空"></label>
                <label><span>登录密码</span><input v-model="configuration.smtp_password" name="smtp_password" type="password" maxlength="256" autocomplete="new-password" :placeholder="configuration.smtp_password_configured ? '已配置，留空保留原密码' : '邮箱密码或客户端专用密码'"></label>
                <label><span>发件人邮箱</span><input v-model.trim="configuration.smtp_from_email" name="smtp_from_email" type="email" inputmode="email" spellcheck="false" autocomplete="off" :required="configuration.smtp_enabled" maxlength="320" placeholder="例如：reports@example.com"></label>
                <label><span>发件人名称</span><input v-model.trim="configuration.smtp_from_name" name="smtp_from_name" autocomplete="off" maxlength="100" placeholder="例如：切割生产智能产报系统"></label>
              </div>
              <div class="security-options">
                <span>连接安全</span>
                <label><input v-model="configuration.smtp_use_tls" name="smtp_use_tls" type="checkbox" @change="selectTls">STARTTLS</label>
                <label><input v-model="configuration.smtp_use_ssl" name="smtp_use_ssl" type="checkbox" @change="selectSsl">SSL/TLS</label>
                <small>按邮箱管理员提供的方式选择；两项互斥，也可都不选。</small>
              </div>
            </fieldset>
          </div>

          <div v-if="configurationMessage" class="configuration-feedback success" role="status" aria-live="polite">{{ configurationMessage }}</div>
          <div v-if="configurationError" class="configuration-feedback error" role="alert">{{ configurationError }}，请检查参数后重试。</div>
          <div class="configuration-actions">
            <span>密码不回显，留空保留已保存的密码。</span>
            <button type="submit" class="primary" :disabled="configurationSaving">{{ configurationSaving ? '保存中…' : '保存自动化参数' }}</button>
          </div>
        </form>
      </section>

      <section ref="editorSection" class="panel automation-editor" aria-labelledby="automation-editor-title">
        <div class="panel-title">
          <div><span><AppIcon name="calendar" tone="muted" :size="17" /></span><h2 id="automation-editor-title">{{ editingId ? '编辑定时任务' : '新建定时任务' }}</h2></div>
          <p>{{ editingId ? '修改后保存生效' : '设置报表与发送计划' }}</p>
        </div>
        <form class="automation-form" @submit.prevent="submit" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
          <div class="automation-fields">
            <label class="wide"><span>任务名称</span><input v-model.trim="form.name" name="schedule_name" autocomplete="off" required maxlength="80"></label>
            <label><span>设备</span><AppSelect v-model="form.device_id" name="schedule_device" required @change="hasUnsavedChanges = true"><option v-for="item in deviceSelection.items.filter(item => !item.type || item.type === 'cutting')" :key="item.id" :value="item.id">{{ item.name }}</option></AppSelect><small>计划固定使用此设备。</small></label>
            <label><span>报表类型</span><AppSelect v-model="form.report_type" name="report_type" @change="hasUnsavedChanges = true"><option v-for="(label,key) in reportLabels" :key="key" :value="key">{{ label }}</option></AppSelect></label>
            <label><span>报表期间</span><AppSelect v-model="form.report_period" name="report_period" @change="hasUnsavedChanges = true"><template v-if="form.report_type === 'leadership'"><option value="previous_month">上月</option><option value="current_month">本月</option></template><template v-else><option value="previous_day">前一日</option><option value="current_day">当日</option></template></AppSelect></label>
            <label><span>执行频率</span><AppSelect v-model="form.frequency" name="frequency" @change="hasUnsavedChanges = true"><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></AppSelect></label>
            <label><span>执行时间</span><input v-model="form.run_time" name="run_time" type="time" autocomplete="off" required></label>
            <label v-if="form.frequency === 'weekly'" class="wide"><span>星期</span><AppSelect v-model.number="form.weekday" name="weekday" @change="hasUnsavedChanges = true"><option v-for="(day,index) in ['一','二','三','四','五','六','日']" :key="day" :value="index + 1">星期{{ day }}</option></AppSelect></label>
            <label v-if="form.frequency === 'monthly'" class="wide"><span>每月执行日</span><input v-model.number="form.month_day" name="month_day" type="number" inputmode="numeric" autocomplete="off" min="1" max="28"></label>
            <label><span>图表类型</span><AppSelect v-model="form.chart_type" name="chart_type" @change="hasUnsavedChanges = true"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></AppSelect></label>
            <label><span>最大尝试次数</span><input v-model.number="form.max_attempts" name="max_attempts" type="number" inputmode="numeric" autocomplete="off" min="1" max="10"></label>
            <label class="wide"><span>收件人</span><textarea v-model="form.recipients_text" name="recipients" inputmode="email" autocomplete="off" spellcheck="false" rows="3" required placeholder="例如：manager@example.com"></textarea><small>每行一个邮箱，或用逗号分隔。</small></label>
            <label class="wide"><span>邮件主题</span><input v-model.trim="form.subject_template" name="subject_template" autocomplete="off" required><small>可用：{report_name}、{report_type}、{report_date}</small></label>
            <div class="automation-switches wide">
              <label><input v-model="form.attach_report" name="attach_report" type="checkbox">附加 HTML 报表</label>
              <label><input v-model="form.enabled" name="schedule_enabled" type="checkbox">启用定时任务</label>
            </div>
          </div>
          <div class="automation-actions wide">
            <span>邮件失败独立重试，不重复生成报表。</span>
            <div><button v-if="editingId" type="button" class="secondary" @click="resetForm">取消编辑</button><button type="submit" class="primary" :disabled="saving">{{ saving ? '保存中…' : editingId ? '保存修改' : '创建任务' }}</button></div>
          </div>
        </form>
      </section>
    </div>

    <section class="panel automation-list" aria-labelledby="automation-schedules-title">
      <div class="panel-title"><div><span><AppIcon name="tasks" tone="muted" :size="17" /></span><h2 id="automation-schedules-title">定时任务</h2></div><p>{{ status ? `${schedules.length} 个计划` : loading ? '正在读取' : '暂不可用' }}<span class="table-scroll-hint">左右滑动查看完整表格</span></p></div>
      <div v-if="loading && !schedules.length" class="empty" role="status">正在读取定时任务…</div>
      <div v-else-if="!status && error" class="configuration-load-error" role="alert"><span>定时任务暂时无法读取。</span><button type="button" @click="loadAll">重新读取</button></div>
      <div v-else-if="!schedules.length" class="empty">尚未创建定时任务，可在上方设置并创建。</div>
      <div v-else class="automation-table-wrap" tabindex="0" role="region" aria-label="定时任务列表，可滚动查看">
        <table v-content-table data-content-columns="center left left left left right right actions"><thead><tr><th>状态</th><th>任务</th><th>报表</th><th>计划</th><th>下次执行</th><th>收件人</th><th>发送 / 失败</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in schedules" :key="item.schedule_id">
            <td><span class="schedule-status" :class="{ enabled: item.enabled }"><i></i>{{ item.enabled ? '启用' : '停用' }}</span></td>
            <td class="task-cell"><strong>{{ item.name }}</strong><small>{{ formatDeviceDisplay(item.device_id) }}</small><small>{{ scheduleLabels[item.last_status] || item.last_status || '尚未执行' }}</small></td>
            <td>{{ reportLabels[item.report_type] }}</td><td>{{ scheduleText(item) }}</td><td>{{ formatDateTime(item.next_run_at) }}</td>
            <td :title="item.recipients.join(', ')">{{ item.recipients.length }} 人</td><td>{{ item.sent_count || 0 }} / {{ item.failed_count || 0 }}</td>
            <td><div class="row-actions"><button type="button" @click="runNow(item)">立即执行</button><button type="button" @click="editSchedule(item)">编辑</button><button type="button" class="danger" @click="remove(item)">删除</button></div></td>
          </tr></tbody></table>
      </div>
    </section>

    <section class="panel automation-list" aria-labelledby="automation-deliveries-title">
      <div class="panel-title"><div><span><AppIcon name="send" tone="muted" :size="17" /></span><h2 id="automation-deliveries-title">邮件发送记录</h2></div><p>失败可重发，未知结果先核对<span class="table-scroll-hint">左右滑动查看完整表格</span></p></div>
      <div v-if="loading && !deliveries.length" class="empty" role="status">正在读取邮件发送记录…</div>
      <div v-else-if="!status && error" class="configuration-load-error" role="alert"><span>邮件发送记录暂时无法读取。</span><button type="button" @click="loadAll">重新读取</button></div>
      <div v-else-if="!deliveries.length" class="empty">尚无邮件发送记录，任务执行后会显示在这里。</div>
      <div v-else class="automation-table-wrap delivery-table-wrap" tabindex="0" role="region" aria-label="邮件发送记录，可滚动查看">
        <table v-content-table data-content-columns="left left left left center right left actions"><thead><tr><th>创建时间</th><th>任务</th><th>报表日期</th><th>收件人</th><th>状态</th><th>尝试</th><th>结果</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in deliveries" :key="item.delivery_id">
            <td>{{ formatDateTime(item.created_at) }}</td>
            <td class="task-cell"><strong>{{ item.schedule_name }}</strong><small :title="item.subject">{{ item.subject }}</small></td>
            <td>{{ formatChineseDate(item.report_date) }}</td>
            <td class="recipient-cell">{{ item.recipients.join(', ') }}<small v-for="(result, address) in item.recipient_results" :key="address" :title="`${address}：${result.status}`">{{ address }}：{{ result.status === 'accepted' ? '服务器已接受' : result.status === 'unknown' ? '结果待核对' : result.status === 'not_accepted' ? '服务器未接收（人工核对）' : '服务器拒收' }}{{ result.code ? `（${result.code}）` : '' }}</small></td>
            <td><span class="delivery-status" :class="item.status">{{ deliveryLabels[item.status] || item.status }}</span></td>
            <td>{{ item.attempt_count }} / {{ item.max_attempts }}</td>
            <td class="error-cell" :title="item.last_error || ''"><span>{{ item.sent_at ? formatDateTime(item.sent_at) : item.last_error || '—' }}</span></td>
            <td><div class="delivery-actions"><button v-if="item.status === 'failed'" type="button" @click="retry(item)">重新发送</button><button type="button" @click="openDelivery(item)">{{ item.status === 'unknown' ? '核对结果' : '核对记录' }}</button></div><small v-if="item.status === 'unknown'" :title="item.message_id">{{ item.message_id || '无历史 Message-ID' }}</small></td>
          </tr></tbody></table>
      </div>
      <div v-if="status" class="automation-pagination app-pagination"><div class="app-pagination__actions"><button type="button" :disabled="deliveryPage <= 1" @click="deliveryPage--; loadAll()">上一页</button><span class="app-pagination__summary">第 {{ deliveryPage }} / {{ pageCount }} 页，共 {{ deliveryTotal }} 条</span><button type="button" :disabled="deliveryPage >= pageCount" @click="deliveryPage++; loadAll()">下一页</button></div></div>
    </section>
    <EmailDeliveryResolution v-if="selectedDelivery" :key="selectedDelivery.delivery_id" :delivery="selectedDelivery" @dirty="resolutionDirty = $event" @resolved="deliveryResolved" @close="selectedDelivery = null; resolutionDirty = false" />
  </div>
</template>

<style scoped>
.automation-page { --automation-border: var(--theme-border-color); }
.automation-page .panel-title { gap: 12px; }
.automation-page .panel-title p { margin: 0; font-size: var(--font-auxiliary); line-height: 1.5; text-align: right; }
.table-scroll-hint { display: none; }
.automation-message, .configuration-feedback { padding: 12px 14px; border: 1px solid; border-radius: 7px; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.automation-message.success, .configuration-feedback.success { color: rgb(var(--success-color)); border-color: rgb(var(--success-color)); background: rgb(var(--success-color) / .1); }
.automation-message.error, .configuration-feedback.error { color: rgb(var(--error-color)); border-color: rgb(var(--error-color)); background: rgb(var(--error-color) / .1); }
.automation-kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
.automation-page .automation-kpis article { min-width: 0; display: flex; align-items: center; gap: 14px; padding: 16px 18px; border: 1px solid var(--automation-border); border-radius: 10px; background: rgb(var(--container-bg-color)); box-shadow: var(--theme-shadow); }
.automation-kpis article > div { min-width: 0; }
.automation-kpis .kpi-icon { width: 38px; height: 38px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 9px; background: rgb(var(--info-color) / .1); }
.automation-kpis span { color: var(--muted); font-size: var(--font-auxiliary); }
.automation-page .automation-kpis strong { display: block; margin-top: 5px; color: var(--text-color-1); font-size: 24px; line-height: 1.25; font-variant-numeric: tabular-nums; }
.automation-kpis em { color: var(--text-color-3); font-size: 18px; font-style: normal; font-weight: 400; }
.automation-workspace { min-width: 0; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: stretch; gap: 12px; }
.automation-workspace > .panel { display: flex; flex-direction: column; min-width: 0; margin: 0; border-color: var(--automation-border); border-radius: 10px; background: rgb(var(--container-bg-color)); box-shadow: var(--theme-shadow); }
.automation-settings, .automation-editor, .automation-list { min-width: 0; overflow: hidden; }
.automation-page > :deep(.resolution-panel) { margin-top: 0; }
.automation-editor { scroll-margin-top: 82px; }
.configuration-form, .automation-form { display: flex; flex-direction: column; flex: 1; padding: 18px; background: rgb(var(--container-bg-color)); }
.configuration-notice { display: flex; align-items: flex-start; gap: 8px; margin-bottom: 18px; padding: 10px 12px; border-radius: 7px; color: var(--muted); background: rgb(var(--primary-color) / .1); font-size: var(--font-auxiliary); line-height: 1.6; }
.configuration-notice .app-icon { flex: 0 0 auto; margin-top: 1px; }
.configuration-grid { display: grid; gap: 18px; margin-bottom: 16px; }
.configuration-card { min-width: 0; margin: 0; padding: 0; border: 0; }
.configuration-card legend { margin-bottom: 12px; padding: 0; color: var(--text-color-1); font-size: 13px; font-weight: 700; }
.smtp-configuration-card { padding-top: 16px; border-top: 1px solid var(--theme-border-color); }
.setting-switch { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; padding: 10px 12px; border: 1px solid var(--theme-border-color); border-radius: 7px; background: rgb(var(--info-color) / .1); }
.setting-switch strong, .setting-switch small { display: block; }
.setting-switch strong { color: var(--text-color-1); font-size: 12px; }
.setting-switch small { margin-top: 3px; color: var(--muted); font-size: var(--font-auxiliary); line-height: 1.5; }
.setting-switch > input { width: 18px; height: 18px; flex: 0 0 auto; accent-color: var(--cyan); }
.setting-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.setting-fields .span-two, .automation-form .wide { grid-column: 1 / -1; }
.setting-fields label, .automation-fields > label { min-width: 0; }
.setting-fields label > span, .automation-form label > span { display: block; margin-bottom: 6px; color: var(--text-color-2); font-size: var(--font-auxiliary); font-weight: 650; }
.setting-fields input, .automation-form input { width: 100%; height: 36px; min-width: 0; padding: 0 10px; }
.automation-form :deep(.app-select) { height: 36px; }
.automation-form :deep(.app-select__trigger) { padding: 0 10px; }
.setting-fields small, .automation-form small { display: block; margin-top: 5px; color: var(--muted); font-size: var(--font-auxiliary); line-height: 1.5; overflow-wrap: anywhere; }
.security-options { display: flex; align-items: center; flex-wrap: wrap; gap: 9px 14px; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--theme-border-color); color: var(--text-color-2); font-size: var(--font-auxiliary); }
.security-options > span { font-weight: 650; }
.security-options label { display: flex; align-items: center; gap: 6px; }
.security-options input { width: 16px; height: 16px; accent-color: var(--cyan); }
.security-options small { flex: 1 1 100%; color: var(--muted); font-size: var(--font-auxiliary); line-height: 1.5; }
.configuration-feedback { margin-top: 14px; }
.configuration-actions, .automation-actions { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-top: auto; padding-top: 14px; border-top: 1px solid var(--theme-border-color); }
.configuration-actions > span, .automation-actions > span { color: var(--muted); font-size: var(--font-auxiliary); line-height: 1.5; }
.configuration-actions button, .automation-actions button { min-height: 36px; padding: 0 14px; }
.configuration-actions button { margin-left: auto; }
.automation-actions > div { display: flex; gap: 8px; margin-left: auto; }
.automation-form { gap: 14px; }
.automation-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px 12px; }
.automation-form textarea { width: 100%; min-height: 80px; resize: vertical; padding: 9px 10px; border: 1px solid var(--theme-border-color); border-radius: 7px; color: var(--ink); background: rgb(var(--container-bg-color)); font: inherit; font-size: var(--font-auxiliary); }
.automation-switches { display: flex; flex-wrap: wrap; gap: 12px 18px; padding: 12px; border-radius: 7px; background: rgb(var(--info-color) / .1); }
.automation-switches label { display: flex; align-items: center; gap: 7px; color: var(--text-color-2); font-size: var(--font-auxiliary); }
.automation-switches input { width: 16px; height: 16px; accent-color: var(--cyan); }
.configuration-load-error { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin: 18px; padding: 12px; border: 1px solid rgb(var(--error-color)); border-radius: 7px; color: rgb(var(--error-color)); background: rgb(var(--error-color) / .1); font-size: 12px; }
.configuration-load-error button { min-height: 32px; padding: 0 10px; }
.automation-page .empty { padding: 30px 18px; color: var(--muted); font-size: 12px; line-height: 1.6; text-align: center; }
.automation-table-wrap { max-height: 340px; overflow: auto; scrollbar-gutter: stable; }
.delivery-table-wrap { max-height: 440px; }
.automation-table-wrap:focus-visible { outline: 2px solid rgb(var(--primary-color) / .3); outline-offset: -2px; }
.automation-table-wrap table { width: 100%; border-collapse: collapse; }
.automation-page .automation-table-wrap th { position: sticky; z-index: 1; top: 0; font-size: var(--font-auxiliary) !important; }
.automation-table-wrap td strong, .automation-table-wrap td small { display: block; }
.automation-table-wrap td small { margin-top: 4px; color: var(--muted); font-size: var(--font-auxiliary); }
.automation-page .automation-table-wrap td.task-cell, .automation-page .automation-table-wrap td.recipient-cell, .automation-page .automation-table-wrap td.error-cell { text-align: left !important; }
.automation-page .automation-table-wrap td.task-cell strong, .automation-page .automation-table-wrap td.task-cell small, .automation-page .automation-table-wrap td.recipient-cell small { text-align: left !important; }
.automation-page .automation-table-wrap td.error-cell { white-space: nowrap !important; }
.error-cell > span { display: block; color: var(--muted); line-height: 1.6; overflow-wrap: anywhere; }
.schedule-status { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 5px; color: var(--muted); background: rgb(var(--primary-color) / .1); }
.schedule-status i { width: 6px; height: 6px; border-radius: 50%; background: var(--theme-hover-color); }
.schedule-status.enabled { color: var(--text-color-1); background: rgb(var(--success-color) / .1); }
.schedule-status.enabled i { background: rgb(var(--success-color)); }
.row-actions, .delivery-actions { display: flex; justify-content: center; gap: 6px; }
.row-actions button, .delivery-actions button { min-height: 30px; padding: 0 9px; font-size: var(--font-auxiliary); white-space: nowrap; }
.row-actions .danger { color: rgb(var(--error-color)); border-color: rgb(var(--error-color)); background: rgb(var(--error-color) / .1); }
.delivery-status { display: inline-block; padding: 4px 8px; border-radius: 5px; color: var(--muted); background: rgb(var(--info-color) / .1); }
.delivery-status.sent { color: var(--text-color-1); background: rgb(var(--success-color) / .1); }
.delivery-status.failed { color: rgb(var(--error-color)); background: rgb(var(--error-color) / .1); }
.delivery-status.retry_wait, .delivery-status.unknown { color: var(--text-color-1); background: rgb(var(--warning-color) / .1); }
.delivery-status.sending { color: rgb(var(--info-color)); background: rgb(var(--info-color) / .1); }
.automation-pagination { display: flex; align-items: center; justify-content: flex-end; padding: 12px 16px; border-top: 1px solid var(--theme-border-color); color: var(--muted); font-size: var(--font-auxiliary); }
.automation-pagination button { min-height: 32px; padding: 0 10px; }
@media (max-width: 1100px) {
  .automation-workspace { grid-template-columns: 1fr; }
  .configuration-grid { grid-template-columns: minmax(0, .8fr) minmax(0, 1.2fr); gap: 18px; }
  .smtp-configuration-card { padding-top: 0; padding-left: 18px; border-top: 0; border-left: 1px solid var(--theme-border-color); }
}
@media (max-width: 760px) {
  .automation-page .panel-title { align-items: flex-start; flex-direction: column; justify-content: center; gap: 5px; padding-block: 12px; }
  .automation-page .panel-title p { text-align: left; }
  .automation-page .panel-title .table-scroll-hint { display: inline-block; margin-left: 10px; color: var(--muted); font-size: var(--font-auxiliary); font-weight: 400; }
  .automation-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .automation-page .automation-kpis article { gap: 10px; padding: 14px 12px; }
  .automation-kpis .kpi-icon { width: 32px; height: 32px; }
  .configuration-grid { grid-template-columns: 1fr; }
  .smtp-configuration-card { padding-top: 16px; padding-left: 0; border-top: 1px solid var(--theme-border-color); border-left: 0; }
  .configuration-form, .automation-form { padding: 16px; }
}
@media (max-width: 560px) {
  .automation-page .automation-kpis article { gap: 8px; padding: 12px 10px; }
  .automation-kpis .kpi-icon { width: 28px; height: 28px; }
  .automation-page .automation-kpis strong { font-size: 22px; }
  .setting-fields, .automation-fields { grid-template-columns: 1fr; }
  .setting-fields .span-two, .automation-form .wide { grid-column: auto; }
  .configuration-actions, .automation-actions { align-items: stretch; flex-direction: column; }
  .configuration-actions button, .automation-actions > div { width: 100%; margin-left: 0; }
  .automation-actions button { flex: 1; }
  .automation-pagination { padding-inline: 12px; }
}
</style>
