<script setup lang="ts">
import { NTimePicker } from 'naive-ui'
import { useConfirm } from '../composables/useConfirm.ts'
import { useAutomationConfiguration } from '../composables/useAutomationConfiguration'
import { useAutomationSchedules } from '../composables/useAutomationSchedules'
import { useAutomationDeliveries } from '../composables/useAutomationDeliveries'

import type { AutomationSchedule, AutomationStatus } from '../types/automation'

import { toError } from '../utils/errors'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { getAutomationStatus, listAutomationSchedules } from '../api/automation.ts'
import { deviceSelection } from '../state/devices.ts'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import AppIcon from '../components/AppIcon.vue'
import AutomationScheduleTable from '../components/automation/AutomationScheduleTable.vue'
import AutomationDeliveryTable from '../components/automation/AutomationDeliveryTable.vue'
import EmailDeliveryResolution from '../components/EmailDeliveryResolution.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.ts'

const confirm = useConfirm()
const status = ref<AutomationStatus | null>(null)
const schedules = ref<AutomationSchedule[]>([])
let loadSequence = 0
let disposed = false
const loading = ref(false)
const message = ref('')
const error = ref('')
const editorSection = ref<HTMLElement | null>(null)
const hasUnsavedChanges = ref(false)
const {
  configurationLoading, configurationLoaded, configurationSaving, configurationMessage, configurationError,
  configuration, selectTls, selectSsl, loadConfiguration, submitConfiguration,
} = useAutomationConfiguration({ hasUnsavedChanges, refresh: loadAll })
const {
  saving, editingId, form, changeReportType, resetForm,
  editSchedule, submit, runNow, remove,
} = useAutomationSchedules({
  hasUnsavedChanges, message, error, refresh: loadAll, confirm,
  scrollToEditor: () => nextTick(() => editorSection.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })),
})
const {
  deliveries, deliveryPage, deliveryTotal, deliveryLoading, selectedDelivery,
  resolutionDirty, pageCount, loadDeliveries, changeDeliveryPage, retry,
  openDelivery, deliveryResolved, pauseDeliveries,
} = useAutomationDeliveries({ message, error, refresh: loadAll, isDisposed: () => disposed, confirm })
useUnsavedChanges(computed(() => hasUnsavedChanges.value || resolutionDirty.value), '自动化配置还有未保存的修改，确定离开吗？')
const reportLabels: Record<string, string> = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表' }
const deliveryLabels: Record<string, string> = {
  pending: '等待发送', sending: '发送中', retry_wait: '等待重试', sent: '已发送', failed: '发送失败', unknown: '结果待核对',
}
const scheduleLabels: Record<string, string> = { ...deliveryLabels, queued: '等待生成报表' }

async function loadAll() {
  if (disposed) return
  const sequence = ++loadSequence
  loading.value = true
  error.value = ''
  try {
    const [statusData, scheduleData] = await Promise.all([
      getAutomationStatus(),
      listAutomationSchedules(),
      loadDeliveries(),
    ])
    if (disposed || sequence !== loadSequence) return
    status.value = statusData
    schedules.value = scheduleData.items
  } catch (caught) {
    const exception = toError(caught)
    if (!disposed && sequence === loadSequence) error.value = exception.message
  } finally {
    if (sequence === loadSequence) loading.value = false
  }
}

onMounted(() => Promise.all([loadConfiguration(), loadAll()]))
onBeforeUnmount(() => {
  disposed = true
  loadSequence += 1
  pauseDeliveries()
})
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
          <NButton attr-type="button" @click="loadConfiguration">重新读取</NButton>
        </div>
        <form v-else class="configuration-form" @submit.prevent="submitConfiguration" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
          <div class="configuration-notice">
            <AppIcon name="check" tone="muted" :size="18" />
            <span>保存后用于新任务与新邮件，正在执行的任务不受影响。</span>
          </div>

          <div class="configuration-grid">
            <fieldset class="configuration-card">
              <legend>任务引擎</legend>
              <NCheckbox v-model:checked="configuration.automation_enabled" name="automation_enabled" @update:checked="hasUnsavedChanges = true"  class="setting-switch"><span><strong>启用报表自动化</strong><small>关闭后保留计划，暂停自动执行</small></span></NCheckbox>
              <div class="setting-fields two-columns">
                <label><span>计划扫描间隔（秒）</span><NInputNumber v-model:value="configuration.automation_scan_seconds" name="automation_scan_seconds" inputmode="numeric" autocomplete="off" :min="1" :max="3600" :step="1" required @update:value="hasUnsavedChanges = true" :input-props="{ 'name': 'automation_scan_seconds', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /><small>建议 15 秒，范围 1–3600 秒</small></label>
                <label><span>重试基准间隔（秒）</span><NInputNumber v-model:value="configuration.email_retry_base_seconds" name="email_retry_base_seconds" inputmode="numeric" autocomplete="off" :min="1" :max="86400" :step="1" required @update:value="hasUnsavedChanges = true" :input-props="{ 'name': 'email_retry_base_seconds', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /><small>后续重试间隔逐次延长</small></label>
              </div>
            </fieldset>

            <fieldset class="configuration-card smtp-configuration-card">
              <legend>SMTP 邮件服务</legend>
              <NCheckbox v-model:checked="configuration.smtp_enabled" name="smtp_enabled" @update:checked="hasUnsavedChanges = true"  class="setting-switch"><span><strong>启用 SMTP 发送</strong><small>关闭时仍可编辑并保存参数</small></span></NCheckbox>
              <div class="setting-fields smtp-fields">
                <label class="span-two"><span>SMTP 服务器</span><NInput v-model:value.trim="configuration.smtp_host" name="smtp_host" autocomplete="off" spellcheck="false" :required="configuration.smtp_enabled" :maxlength="255" placeholder="例如：mail.example.com" aria-describedby="smtp-host-hint" :input-props="{ 'name': 'smtp_host', 'autocomplete': 'off', 'spellcheck': false, 'required': configuration.smtp_enabled, 'aria-describedby': 'smtp-host-hint' }" /><small id="smtp-host-hint">填写服务器域名或 IP，不填 https:// 或网页路径。</small></label>
                <label><span>端口</span><NInputNumber v-model:value="configuration.smtp_port" name="smtp_port" inputmode="numeric" autocomplete="off" :min="1" :max="65535" required @update:value="hasUnsavedChanges = true" :input-props="{ 'name': 'smtp_port', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /></label>
                <label><span>超时（秒）</span><NInputNumber v-model:value="configuration.smtp_timeout_seconds" name="smtp_timeout_seconds" inputmode="numeric" autocomplete="off" :min="1" :max="300" :step="1" required @update:value="hasUnsavedChanges = true" :input-props="{ 'name': 'smtp_timeout_seconds', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /></label>
                <label><span>登录账号</span><NInput v-model:value.trim="configuration.smtp_username" name="smtp_username" :maxlength="255" autocomplete="username" spellcheck="false" placeholder="填写邮箱账号，免认证时可留空" :input-props="{ 'name': 'smtp_username', 'autocomplete': 'username', 'spellcheck': false }" /></label>
                <label><span>登录密码</span><NInput v-model:value="configuration.smtp_password" name="smtp_password" type="password" show-password-on="click" :maxlength="256" autocomplete="new-password" :placeholder="configuration.smtp_password_configured ? '已配置，留空保留原密码' : '邮箱密码或客户端专用密码'" :input-props="{ 'name': 'smtp_password', 'autocomplete': 'new-password' }" /></label>
                <label><span>发件人邮箱</span><NInput v-model:value.trim="configuration.smtp_from_email" name="smtp_from_email" inputmode="email" spellcheck="false" autocomplete="off" :required="configuration.smtp_enabled" :maxlength="320" placeholder="例如：reports@example.com" :input-props="{ 'name': 'smtp_from_email', type: 'email', 'inputmode': 'email', 'spellcheck': false, 'autocomplete': 'off', 'required': configuration.smtp_enabled }" /></label>
                <label><span>发件人名称</span><NInput v-model:value.trim="configuration.smtp_from_name" name="smtp_from_name" autocomplete="off" :maxlength="100" placeholder="例如：切割生产智能产报系统" :input-props="{ 'name': 'smtp_from_name', 'autocomplete': 'off' }" /></label>
              </div>
              <div class="security-options">
                <span>连接安全</span>
                <NCheckbox v-model:checked="configuration.smtp_use_tls" name="smtp_use_tls" @update:checked="selectTls(); hasUnsavedChanges = true" >STARTTLS</NCheckbox>
                <NCheckbox v-model:checked="configuration.smtp_use_ssl" name="smtp_use_ssl" @update:checked="selectSsl(); hasUnsavedChanges = true" >SSL/TLS</NCheckbox>
                <small>按邮箱管理员提供的方式选择；两项互斥，也可都不选。</small>
              </div>
            </fieldset>
          </div>

          <div v-if="configurationMessage" class="configuration-feedback success" role="status" aria-live="polite">{{ configurationMessage }}</div>
          <div v-if="configurationError" class="configuration-feedback error" role="alert">{{ configurationError }}，请检查参数后重试。</div>
          <div class="configuration-actions">
            <span>密码不回显，留空保留已保存的密码。</span>
            <NButton attr-type="submit" class="primary" :disabled="configurationSaving" type="primary" :loading="configurationSaving">{{ configurationSaving ? '保存中…' : '保存自动化参数' }}</NButton>
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
            <label class="wide"><span>任务名称</span><NInput v-model:value.trim="form.name" name="schedule_name" autocomplete="off" required :maxlength="80" :input-props="{ 'name': 'schedule_name', 'autocomplete': 'off', 'required': true }" /></label>
            <label><span>设备</span><AppSelect v-model="form.device_id" name="schedule_device" required @change="hasUnsavedChanges = true"><option v-for="item in deviceSelection.items.filter(item => !item.type || item.type === 'cutting')" :key="item.id" :value="item.id">{{ item.name }}</option></AppSelect><small>计划固定使用此设备。</small></label>
            <label><span>报表类型</span><AppSelect v-model="form.report_type" name="report_type" @change="changeReportType"><option v-for="(label,key) in reportLabels" :key="key" :value="key">{{ label }}</option></AppSelect></label>
            <label><span>报表期间</span><AppSelect v-model="form.report_period" name="report_period" @change="hasUnsavedChanges = true"><template v-if="form.report_type === 'leadership'"><option value="previous_month">上月</option><option value="current_month">本月</option></template><template v-else><option value="previous_day">前一日</option><option value="current_day">当日</option></template></AppSelect></label>
            <label><span>执行频率</span><AppSelect v-model="form.frequency" name="frequency" @change="hasUnsavedChanges = true"><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></AppSelect></label>
            <label><span>执行时间</span><NTimePicker v-model:formatted-value="form.run_time" name="run_time" autocomplete="off" required @update:formatted-value="hasUnsavedChanges = true" format="HH:mm" value-format="HH:mm" :clearable="false" input-readonly /></label>
            <label v-if="form.frequency === 'weekly'" class="wide"><span>星期</span><AppSelect v-model.number="form.weekday" name="weekday" @change="hasUnsavedChanges = true"><option v-for="(day,index) in ['一','二','三','四','五','六','日']" :key="day" :value="index + 1">星期{{ day }}</option></AppSelect></label>
            <label v-if="form.frequency === 'monthly'" class="wide"><span>每月执行日</span><NInputNumber v-model:value="form.month_day" name="month_day" inputmode="numeric" autocomplete="off" :min="1" :max="28" @update:value="hasUnsavedChanges = true" :input-props="{ 'name': 'month_day', 'inputmode': 'numeric', 'autocomplete': 'off' }" :precision="0" /></label>
            <label><span>图表类型</span><AppSelect v-model="form.chart_type" name="chart_type" @change="hasUnsavedChanges = true"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></AppSelect></label>
            <label><span>最大尝试次数</span><NInputNumber v-model:value="form.max_attempts" name="max_attempts" inputmode="numeric" autocomplete="off" :min="1" :max="10" @update:value="hasUnsavedChanges = true" :input-props="{ 'name': 'max_attempts', 'inputmode': 'numeric', 'autocomplete': 'off' }" :precision="0" /></label>
            <label class="wide"><span>收件人</span><NInput v-model:value="form.recipients_text" name="recipients" inputmode="email" autocomplete="off" spellcheck="false" required placeholder="例如：manager@example.com" type="textarea" show-count :autosize="{ minRows: 3, maxRows: 8 }" :input-props="{ 'name': 'recipients', 'inputmode': 'email', 'autocomplete': 'off', 'spellcheck': false, 'required': true }" /><small>每行一个邮箱，或用逗号分隔。</small></label>
            <label class="wide"><span>邮件主题</span><NInput v-model:value.trim="form.subject_template" name="subject_template" autocomplete="off" required :input-props="{ 'name': 'subject_template', 'autocomplete': 'off', 'required': true }" /><small>可用：{report_name}、{report_type}、{report_date}</small></label>
            <div class="automation-switches wide">
              <NCheckbox v-model:checked="form.attach_report" name="attach_report" @update:checked="hasUnsavedChanges = true" >附加 HTML 报表</NCheckbox>
              <NCheckbox v-model:checked="form.enabled" name="schedule_enabled" @update:checked="hasUnsavedChanges = true" >启用定时任务</NCheckbox>
            </div>
          </div>
          <div class="automation-actions wide">
            <span>邮件失败独立重试，不重复生成报表。</span>
            <div><NButton v-if="editingId" attr-type="button" class="secondary" @click="resetForm">取消编辑</NButton><NButton attr-type="submit" class="primary" :disabled="saving" type="primary" :loading="saving">{{ saving ? '保存中…' : editingId ? '保存修改' : '创建任务' }}</NButton></div>
          </div>
        </form>
      </section>
    </div>

    <section class="panel automation-list" aria-labelledby="automation-schedules-title">
      <div class="panel-title"><div><span><AppIcon name="tasks" tone="muted" :size="17" /></span><h2 id="automation-schedules-title">定时任务</h2></div><p>{{ status ? `${schedules.length} 个计划` : loading ? '正在读取' : '暂不可用' }}<span class="table-scroll-hint">左右滑动查看完整表格</span></p></div>
      <div v-if="loading && !schedules.length" class="empty" role="status">正在读取定时任务…</div>
      <div v-else-if="!status && error" class="configuration-load-error" role="alert"><span>定时任务暂时无法读取。</span><NButton attr-type="button" @click="loadAll">重新读取</NButton></div>
      <div v-else class="automation-table-wrap">
        <AutomationScheduleTable :schedules="schedules" :loading="loading" :report-labels="reportLabels" :schedule-labels="scheduleLabels" @run="runNow" @edit="editSchedule" @remove="remove" />
      </div>
    </section>

    <section class="panel automation-list" aria-labelledby="automation-deliveries-title">
      <div class="panel-title"><div><span><AppIcon name="send" tone="muted" :size="17" /></span><h2 id="automation-deliveries-title">邮件发送记录</h2></div><p>失败可重发，未知结果先核对<span class="table-scroll-hint">左右滑动查看完整表格</span></p></div>
      <div v-if="deliveryLoading && !deliveries.length" class="empty" role="status">正在读取邮件发送记录…</div>
      <div v-else-if="error && !deliveries.length" class="configuration-load-error" role="alert"><span>邮件发送记录暂时无法读取。</span><NButton attr-type="button" @click="loadAll">重新读取</NButton></div>
      <div v-else class="automation-table-wrap delivery-table-wrap">
        <AutomationDeliveryTable :deliveries="deliveries" :delivery-total="deliveryTotal" :delivery-page="deliveryPage" :delivery-loading="deliveryLoading" :paginate="!!status" :delivery-labels="deliveryLabels" @page="changeDeliveryPage" @retry="retry" @open="openDelivery" />
      </div>
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
.automation-table-wrap { min-width: 0; padding: 0 16px; }
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
