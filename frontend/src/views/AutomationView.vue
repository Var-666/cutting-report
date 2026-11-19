<script setup lang="ts">
import { NAlert, NDrawer, NDrawerContent, NTag, NTimePicker } from 'naive-ui'
import { useConfirm } from '../composables/useConfirm.ts'
import { useAutomationConfiguration } from '../composables/useAutomationConfiguration'
import { useAutomationSchedules } from '../composables/useAutomationSchedules'
import { useAutomationDeliveries } from '../composables/useAutomationDeliveries'

import type { AutomationSchedule, AutomationStatus } from '../types/automation'

import { toError } from '../utils/errors'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getAutomationStatus, listAutomationSchedules } from '../api/automation.ts'
import { deviceSelection } from '../state/devices.ts'
import HomeKpiCard from '../components/HomeKpiCard.vue'
import AppIcon from '../components/AppIcon.vue'
import AutomationScheduleTable from '../components/automation/AutomationScheduleTable.vue'
import AutomationRunProgress from '../components/automation/AutomationRunProgress.vue'
import AutomationDeliveryTable from '../components/automation/AutomationDeliveryTable.vue'
import EmailDeliveryResolution from '../components/EmailDeliveryResolution.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.ts'

const confirm = useConfirm()
const status = ref<AutomationStatus | null>(null)
const schedules = ref<AutomationSchedule[]>([])
const scheduleSearch = ref('')
const appliedScheduleSearch = ref('')
const visibleSchedules = computed(() => schedules.value.filter(item => item.name?.toLocaleLowerCase().includes(appliedScheduleSearch.value.toLocaleLowerCase())))
function searchSchedules() { appliedScheduleSearch.value = scheduleSearch.value.trim() }
function clearScheduleSearch() { scheduleSearch.value = ''; appliedScheduleSearch.value = '' }
let loadSequence = 0
let disposed = false
const loading = ref(false)
const message = ref('')
const error = ref('')
const activePanel = ref<'configuration' | 'schedule' | null>(null)
const configurationDirty = ref(false)
const scheduleDirty = ref(false)
const {
  configurationLoading, configurationLoaded, configurationSaving, configurationMessage, configurationError,
  configuration, selectTls, selectSsl, loadConfiguration, submitConfiguration,
} = useAutomationConfiguration({ hasUnsavedChanges: configurationDirty, refresh: loadAll })
const {
  saving, editingId, form, executions, runningScheduleIds, changeReportType, resetForm, scheduleError,
  editSchedule: fillSchedule, submit: saveSchedule, runNow, remove,
  recipientInputVisible, recipientDraft, recipientError, addRecipient, removeRecipient, cancelRecipientInput,
} = useAutomationSchedules({ hasUnsavedChanges: scheduleDirty, message, error, refresh: loadAll, confirm })
const {
  deliveries, deliveryPage, deliveryTotal, deliveryLoading, selectedDelivery,
  deliverySearch, appliedDeliverySearch, searchDeliveries, clearDeliverySearch,
  resolutionDirty, pageCount, loadDeliveries, changeDeliveryPage, retry,
  openDelivery, deliveryResolved, pauseDeliveries,
} = useAutomationDeliveries({ message, error, refresh: loadAll, isDisposed: () => disposed, confirm })
const scheduleHasDraft = computed(() => scheduleDirty.value || Boolean(recipientDraft.value.trim()))
const hasUnsavedChanges = computed(() => configurationDirty.value || scheduleHasDraft.value)
useUnsavedChanges(computed(() => hasUnsavedChanges.value || resolutionDirty.value), '自动化配置还有未保存的修改，确定离开吗？')

async function openScheduleEditor(item?: AutomationSchedule) {
  const sameDraft = editingId.value === (item?.schedule_id ?? '')
  if (!sameDraft && scheduleHasDraft.value && !await confirm('切换任务会丢弃当前未保存的修改，确定继续吗？')) return
  if (!sameDraft || !scheduleHasDraft.value) {
    if (item) fillSchedule(item)
    else resetForm()
  }
  activePanel.value = 'schedule'
}

async function submit() {
  if (await saveSchedule()) activePanel.value = null
}

async function cancelScheduleEditing() {
  if (scheduleHasDraft.value && !await confirm('确定放弃这次任务编辑？')) return
  resetForm()
  activePanel.value = null
}

const smtp = computed(() => {
  if (!status.value) return { label: loading.value ? '正在读取' : '状态未知', type: 'default' as const }
  if (!status.value.smtp?.enabled) return { label: '发送已停用', type: 'default' as const }
  return status.value.smtp.configured
    ? { label: '已配置', type: 'success' as const }
    : { label: '待配置', type: 'warning' as const }
})
const metrics = computed(() => {
  const summary = status.value?.summary
  return [
    { title: '已启用计划', value: summary?.schedule_enabled, icon: 'calendar', color: 'rgb(var(--primary-color))' },
    { title: '等待 / 重试', value: summary?.delivery_pending, icon: 'clock', color: 'rgb(var(--warning-color))' },
    { title: '已发送', value: summary?.delivery_sent, icon: 'send', color: 'rgb(var(--success-color))' },
    { title: '失败 / 待核对', value: summary && `${summary.delivery_failed} / ${summary.delivery_unknown}`, icon: 'alert', color: 'rgb(var(--error-color))' },
  ]
})
const reportLabels: Record<string, string> = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表' }
const deliveryLabels: Record<string, string> = {
  pending: '等待发送', sending: '发送中', retry_wait: '等待重试', analysis_wait: '等待分析生成', sent: '已发送', failed: '发送失败', unknown: '结果待核对',
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
    <header class="automation-header">
      <div class="automation-heading">
        <span class="automation-heading-icon"><AppIcon name="send" :size="24" tone="current" /></span>
        <div>
          <h1>报表推送</h1>
          <div class="automation-status">
            <NTag size="small" :bordered="false" :type="smtp.type">SMTP {{ smtp.label }}</NTag>
            <span v-if="status?.smtp?.configured" class="smtp-address">{{ status.smtp.host }}:{{ status.smtp.port }}</span>
            <NTag v-if="status && !status.automation_enabled" size="small" type="warning" :bordered="false">自动化已暂停</NTag>
          </div>
        </div>
      </div>
      <div class="automation-toolbar">
        <NButton size="large" aria-haspopup="dialog" @click="activePanel = 'configuration'">
          <template #icon><AppIcon name="settings" tone="current" /></template>运行与邮件参数
        </NButton>
        <NButton size="large" type="primary" aria-haspopup="dialog" @click="openScheduleEditor()">
          <template #icon><AppIcon name="calendar" tone="current" /></template>新建定时任务
        </NButton>
      </div>
    </header>

    <section class="automation-overview" aria-label="报表推送概况" :aria-busy="loading">
      <HomeKpiCard v-for="metric in metrics" :key="metric.title" v-bind="metric" class="automation-metric"
        :value="metric.value ?? null" :loading="loading && !status" trend-label="" />
    </section>

    <NAlert v-if="message" type="success" role="status">{{ message }}</NAlert>
    <NAlert v-if="error" type="error" role="alert">{{ error }}</NAlert>

    <NDrawer :show="activePanel === 'configuration'" width="min(640px, 100vw)" aria-label="运行与邮件参数" :mask-closable="false" :close-on-esc="!configurationSaving"
      @update:show="shown => { if (!shown) activePanel = null }">
      <NDrawerContent title="运行与邮件参数" :closable="!configurationSaving" :body-content-style="{ padding: 'var(--space-4)' }">
        <div v-if="configurationLoading" class="automation-empty" role="status">正在读取自动化配置…</div>
        <div v-else-if="!configurationLoaded" class="configuration-load-error" role="alert">
          <span>{{ configurationError || '自动化配置读取失败' }}</span>
          <NButton attr-type="button" @click="loadConfiguration">重新读取</NButton>
        </div>
        <form v-else id="automation-configuration-form" class="configuration-form" @submit.prevent="submitConfiguration" @input.capture="configurationDirty = true" @change.capture="configurationDirty = true">
          <div class="configuration-notice">
            <AppIcon name="check" tone="muted" :size="18" />
            <span>保存后即时生效，适用于之后启动的任务和邮件。</span>
          </div>

          <div class="configuration-grid">
            <fieldset class="configuration-card">
              <legend>任务引擎</legend>
              <NCheckbox v-model:checked="configuration.automation_enabled" name="automation_enabled" @update:checked="configurationDirty = true"  class="setting-switch"><span><strong>启用报表自动化</strong><small>关闭后保留计划，暂停自动执行</small></span></NCheckbox>
              <div class="setting-fields two-columns">
                <label><span>计划扫描间隔（秒）</span><NInputNumber v-model:value="configuration.automation_scan_seconds" name="automation_scan_seconds" inputmode="numeric" autocomplete="off" :min="1" :max="3600" :step="1" required @update:value="configurationDirty = true" :input-props="{ 'name': 'automation_scan_seconds', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /><small>建议 15 秒，范围 1–3600 秒</small></label>
                <label><span>重试基准间隔（秒）</span><NInputNumber v-model:value="configuration.email_retry_base_seconds" name="email_retry_base_seconds" inputmode="numeric" autocomplete="off" :min="1" :max="86400" :step="1" required @update:value="configurationDirty = true" :input-props="{ 'name': 'email_retry_base_seconds', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /><small>后续重试间隔逐次延长</small></label>
              </div>
            </fieldset>

            <fieldset class="configuration-card smtp-configuration-card">
              <legend>SMTP 邮件服务</legend>
              <NCheckbox v-model:checked="configuration.smtp_enabled" name="smtp_enabled" @update:checked="configurationDirty = true"  class="setting-switch"><span><strong>启用 SMTP 发送</strong><small>关闭时仍可编辑并保存参数</small></span></NCheckbox>
              <div class="setting-fields smtp-fields">
                <label class="span-two"><span>SMTP 服务器</span><NInput v-model:value.trim="configuration.smtp_host" name="smtp_host" autocomplete="off" spellcheck="false" :required="configuration.smtp_enabled" :maxlength="255" placeholder="例如：mail.example.com" aria-describedby="smtp-host-hint" :input-props="{ 'name': 'smtp_host', 'autocomplete': 'off', 'spellcheck': false, 'required': configuration.smtp_enabled, 'aria-describedby': 'smtp-host-hint' }" /><small id="smtp-host-hint">填写服务器域名或 IP，不填 https:// 或网页路径。</small></label>
                <label><span>端口</span><NInputNumber v-model:value="configuration.smtp_port" name="smtp_port" inputmode="numeric" autocomplete="off" :min="1" :max="65535" required @update:value="configurationDirty = true" :input-props="{ 'name': 'smtp_port', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /></label>
                <label><span>超时（秒）</span><NInputNumber v-model:value="configuration.smtp_timeout_seconds" name="smtp_timeout_seconds" inputmode="numeric" autocomplete="off" :min="1" :max="300" :step="1" required @update:value="configurationDirty = true" :input-props="{ 'name': 'smtp_timeout_seconds', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /></label>
                <label><span>登录账号</span><NInput v-model:value.trim="configuration.smtp_username" name="smtp_username" :maxlength="255" autocomplete="username" spellcheck="false" placeholder="填写邮箱账号，免认证时可留空" :input-props="{ 'name': 'smtp_username', 'autocomplete': 'username', 'spellcheck': false }" /></label>
                <label><span>登录密码</span><NInput v-model:value="configuration.smtp_password" name="smtp_password" type="password" show-password-on="click" :maxlength="256" autocomplete="new-password" :placeholder="configuration.smtp_password_configured ? '已配置，留空保留原密码' : '邮箱密码或客户端专用密码'" :input-props="{ 'name': 'smtp_password', 'autocomplete': 'new-password' }" /></label>
                <label><span>发件人邮箱</span><NInput v-model:value.trim="configuration.smtp_from_email" name="smtp_from_email" inputmode="email" spellcheck="false" autocomplete="off" :required="configuration.smtp_enabled" :maxlength="320" placeholder="例如：reports@example.com" :input-props="{ 'name': 'smtp_from_email', type: 'email', 'inputmode': 'email', 'spellcheck': false, 'autocomplete': 'off', 'required': configuration.smtp_enabled }" /></label>
                <label><span>发件人名称</span><NInput v-model:value.trim="configuration.smtp_from_name" name="smtp_from_name" autocomplete="off" :maxlength="100" placeholder="例如：切割生产智能产报系统" :input-props="{ 'name': 'smtp_from_name', 'autocomplete': 'off' }" /></label>
              </div>
              <div class="security-options">
                <span>连接安全</span>
                <NCheckbox v-model:checked="configuration.smtp_use_tls" name="smtp_use_tls" @update:checked="selectTls(); configurationDirty = true" >STARTTLS</NCheckbox>
                <NCheckbox v-model:checked="configuration.smtp_use_ssl" name="smtp_use_ssl" @update:checked="selectSsl(); configurationDirty = true" >SSL/TLS</NCheckbox>
                <small>按邮箱管理员提供的方式选择；两项互斥，也可都不选。</small>
              </div>
            </fieldset>
          </div>

        </form>
        <template v-if="configurationLoaded && !configurationLoading" #footer>
          <div class="drawer-footer">
            <NAlert v-if="configurationError" type="error" role="alert">{{ configurationError }}</NAlert>
            <NAlert v-if="configurationMessage && !configurationDirty" type="success" role="status">{{ configurationMessage }}</NAlert>
            <div class="drawer-actions">
              <span>密码留空会保留已保存的密码。</span>
              <NButton attr-type="submit" form="automation-configuration-form" type="primary" :disabled="configurationSaving" :loading="configurationSaving">保存自动化参数</NButton>
            </div>
          </div>
        </template>
      </NDrawerContent>
    </NDrawer>

    <NDrawer :show="activePanel === 'schedule'" width="min(640px, 100vw)" :aria-label="editingId ? '编辑定时任务' : '新建定时任务'" :mask-closable="false" :close-on-esc="!saving"
      @update:show="shown => { if (!shown) activePanel = null }">
      <NDrawerContent :title="editingId ? '编辑定时任务' : '新建定时任务'" :closable="!saving" :body-content-style="{ padding: 'var(--space-4)' }">
        <form id="automation-schedule-form" class="automation-form" @submit.prevent="submit" @input.capture="scheduleDirty = true" @change.capture="scheduleDirty = true">
          <div class="automation-fields">
            <label class="wide"><span>任务名称</span><NInput v-model:value.trim="form.name" name="schedule_name" autocomplete="off" required :maxlength="80" :input-props="{ 'name': 'schedule_name', 'autocomplete': 'off', 'required': true }" /></label>
            <label><span>设备</span><AppSelect v-model="form.device_id" name="schedule_device" required @change="scheduleDirty = true"><option v-for="item in deviceSelection.items.filter(item => !item.type || item.type === 'cutting')" :key="item.id" :value="item.id">{{ item.name }}</option></AppSelect><small>计划固定使用此设备。</small></label>
            <label><span>报表类型</span><AppSelect v-model="form.report_type" name="report_type" @change="changeReportType"><option v-for="(label,key) in reportLabels" :key="key" :value="key">{{ label }}</option></AppSelect></label>
            <label><span>报表期间</span><AppSelect v-model="form.report_period" name="report_period" @change="scheduleDirty = true"><template v-if="form.report_type === 'leadership'"><option value="previous_month">上月</option><option value="current_month">本月</option></template><template v-else><option value="previous_day">前一日</option><option value="current_day">当日</option></template></AppSelect></label>
            <label><span>执行频率</span><AppSelect v-model="form.frequency" name="frequency" @change="scheduleDirty = true"><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></AppSelect></label>
            <label><span>执行时间</span><NTimePicker v-model:formatted-value="form.run_time" name="run_time" autocomplete="off" required @update:formatted-value="scheduleDirty = true" format="HH:mm" value-format="HH:mm" :clearable="false" input-readonly /></label>
            <label v-if="form.frequency === 'weekly'" class="wide"><span>星期</span><AppSelect v-model.number="form.weekday" name="weekday" @change="scheduleDirty = true"><option v-for="(day,index) in ['一','二','三','四','五','六','日']" :key="day" :value="index + 1">星期{{ day }}</option></AppSelect></label>
            <label v-if="form.frequency === 'monthly'" class="wide"><span>每月执行日</span><NInputNumber v-model:value="form.month_day" name="month_day" inputmode="numeric" autocomplete="off" :min="1" :max="28" @update:value="scheduleDirty = true" :input-props="{ 'name': 'month_day', 'inputmode': 'numeric', 'autocomplete': 'off' }" :precision="0" /></label>
            <label><span>图表类型</span><AppSelect v-model="form.chart_type" name="chart_type" @change="scheduleDirty = true"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></AppSelect></label>
            <label><span>最大尝试次数</span><NInputNumber v-model:value="form.max_attempts" name="max_attempts" inputmode="numeric" autocomplete="off" :min="1" :max="10" @update:value="scheduleDirty = true" :input-props="{ 'name': 'max_attempts', 'inputmode': 'numeric', 'autocomplete': 'off' }" :precision="0" /></label>
            <div class="recipient-field wide" role="group" aria-labelledby="recipient-label">
              <div id="recipient-label" class="recipient-heading">收件人 <span>{{ form.recipients.length }} / 20</span></div>
              <div class="recipient-tags">
                <NTag v-for="address in form.recipients" :key="address" closable :disabled="saving" :title="address" @close="removeRecipient(address)"><span class="recipient-address">{{ address }}</span></NTag>
                <NButton v-if="!recipientInputVisible" attr-type="button" dashed size="small" :disabled="saving || form.recipients.length >= 20" @click="recipientInputVisible = true">＋ 添加邮箱</NButton>
              </div>
              <div v-if="recipientInputVisible" class="recipient-entry">
                <NInput v-model:value="recipientDraft" autofocus :disabled="saving" :status="recipientError ? 'error' : undefined" placeholder="例如：manager@example.com" :input-props="{ name: 'recipient', inputmode: 'email', autocomplete: 'off', spellcheck: false, 'aria-label': '收件人邮箱', 'aria-invalid': !!recipientError, 'aria-describedby': recipientError ? 'recipient-error' : undefined }" @update:value="recipientError = ''" @keydown.enter.prevent="!$event.isComposing && addRecipient()" @keydown.esc.stop.prevent="cancelRecipientInput" />
                <NButton attr-type="button" :disabled="saving" @click="addRecipient">添加</NButton>
                <NButton attr-type="button" :disabled="saving" @click="cancelRecipientInput">取消</NButton>
              </div>
              <p v-if="recipientError" id="recipient-error" class="recipient-error" role="alert">{{ recipientError }}</p>
            </div>
            <label class="wide"><span>邮件主题</span><NInput v-model:value.trim="form.subject_template" name="subject_template" autocomplete="off" required :input-props="{ 'name': 'subject_template', 'autocomplete': 'off', 'required': true }" /><small>可用：{report_name}、{report_type}、{report_date}</small></label>
            <div class="automation-switches wide">
              <NCheckbox v-model:checked="form.attach_report" name="attach_report" @update:checked="scheduleDirty = true" >附加 HTML 报表</NCheckbox>
              <NCheckbox v-model:checked="form.enabled" name="schedule_enabled" @update:checked="scheduleDirty = true" >启用定时任务</NCheckbox>
            </div>
          </div>
        </form>
        <template #footer>
          <div class="drawer-footer">
            <NAlert v-if="scheduleError" type="error" role="alert">{{ scheduleError }}</NAlert>
            <div class="drawer-actions">
              <span>邮件发送失败时会单独重试。</span>
              <div>
                <NButton :disabled="saving" @click="cancelScheduleEditing">取消</NButton>
                <NButton attr-type="submit" form="automation-schedule-form" type="primary" :disabled="saving" :loading="saving">{{ editingId ? '保存修改' : '创建任务' }}</NButton>
              </div>
            </div>
          </div>
        </template>
      </NDrawerContent>
    </NDrawer>

    <section class="panel automation-list" aria-labelledby="automation-schedules-title">
      <div class="panel-title"><div><span><AppIcon name="tasks" tone="muted" :size="17" /></span><h2 id="automation-schedules-title">定时任务</h2></div><div class="automation-list-tools"><form class="automation-search" role="search" aria-label="定时任务检索" @submit.prevent="searchSchedules"><NInput v-model:value="scheduleSearch" clearable size="small" :maxlength="200" placeholder="检索任务名称" :input-props="{ 'aria-label': '任务名称关键词' }" @clear="clearScheduleSearch" /><NButton attr-type="submit" size="small">检索</NButton></form><p>{{ status ? `${visibleSchedules.length}${appliedScheduleSearch ? ` / ${schedules.length}` : ''} 个计划` : loading ? '正在读取' : '暂不可用' }}</p></div></div>
      <div v-if="loading && !schedules.length" class="empty" role="status">正在读取定时任务…</div>
      <div v-else-if="!status && error" class="configuration-load-error" role="alert"><span>定时任务暂时无法读取。</span><NButton attr-type="button" @click="loadAll">重新读取</NButton></div>
      <div v-else class="automation-table-wrap">
        <AutomationRunProgress v-for="execution in executions" :key="execution.schedule_id" :execution="execution" />
        <AutomationScheduleTable :schedules="visibleSchedules" :searching="!!appliedScheduleSearch" :loading="loading" :running-schedule-ids="runningScheduleIds" :report-labels="reportLabels" :schedule-labels="scheduleLabels" @run="runNow" @edit="openScheduleEditor" @remove="remove" />
      </div>
    </section>

    <section class="panel automation-list" aria-labelledby="automation-deliveries-title">
      <div class="panel-title"><div><span><AppIcon name="send" tone="muted" :size="17" /></span><h2 id="automation-deliveries-title">邮件发送记录</h2></div><div class="automation-list-tools"><form class="automation-search" role="search" aria-label="邮件发送记录检索" @submit.prevent="searchDeliveries"><NInput v-model:value="deliverySearch" clearable size="small" :maxlength="200" placeholder="任务名、邮件主题或收件人" :input-props="{ 'aria-label': '邮件记录关键词' }" @clear="clearDeliverySearch" /><NButton attr-type="submit" size="small" :loading="deliveryLoading">检索</NButton></form><p>失败可重发，未知结果先核对</p></div></div>
      <div v-if="deliveryLoading && !deliveries.length" class="empty" role="status">正在读取邮件发送记录…</div>
      <div v-else-if="error && !deliveries.length" class="configuration-load-error" role="alert"><span>邮件发送记录暂时无法读取。</span><NButton attr-type="button" @click="loadAll">重新读取</NButton></div>
      <div v-else class="automation-table-wrap delivery-table-wrap">
        <AutomationDeliveryTable :deliveries="deliveries" :searching="!!appliedDeliverySearch" :delivery-total="deliveryTotal" :delivery-page="deliveryPage" :delivery-loading="deliveryLoading" :paginate="!!status" :delivery-labels="deliveryLabels" @page="changeDeliveryPage" @retry="retry" @open="openDelivery" />
      </div>
    </section>
    <EmailDeliveryResolution v-if="selectedDelivery" :key="selectedDelivery.delivery_id" :delivery="selectedDelivery" @dirty="resolutionDirty = $event" @resolved="deliveryResolved" @close="selectedDelivery = null; resolutionDirty = false" />
  </div>
</template>

<style scoped>
.automation-page.management-page { gap: var(--space-4); }
.automation-header {
  display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:var(--space-4);
  padding:var(--space-6); border:1px solid var(--theme-border-color); border-radius:var(--report-panel-radius);
  background:linear-gradient(100deg, rgb(var(--primary-color) / .07), transparent 45%), rgb(var(--container-bg-color));
  box-shadow:var(--theme-shadow);
}
.automation-heading { display:flex; align-items:flex-start; gap:var(--space-3); min-width:0; }
.automation-heading > div { min-width:0; }
.automation-heading-icon {
  flex:0 0 auto; display:grid; place-items:center; width:44px; height:44px;
  border-radius:var(--card-radius); color:rgb(var(--primary-color)); background:rgb(var(--primary-color) / .12);
}
.automation-heading h1 { margin:0 0 var(--space-2); font-size:24px; font-weight:700; line-height:1.3; }
.automation-toolbar, .automation-status { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); min-width:0; }
.smtp-address { color:var(--text-color-3); font-size:var(--font-auxiliary); overflow-wrap:anywhere; }
.automation-overview { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:var(--space-3); }
.automation-overview :deep(.automation-metric) {
  min-height:104px; justify-content:start; gap:var(--space-2); border-radius:var(--card-radius);
  border:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow);
}
.automation-overview :deep(.home-kpi__trend) { display:none; }
.automation-page .panel-title { gap:var(--space-3); padding:var(--space-3) var(--space-4); background:transparent; }
.automation-page .panel-title h2 { font-size:var(--font-section-title); }
.automation-page .panel-title p { margin:0; font-size:var(--font-auxiliary); line-height:1.5; text-align:right; }
.automation-list, .automation-table-wrap { min-width:0; }
.automation-table-wrap { padding:var(--space-2) var(--space-4) var(--space-4); }
.automation-table-wrap :deep(.app-report-pagination) { padding:var(--space-4) 0 0; }
.automation-list-tools, .automation-search { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
.automation-search :deep(.n-input) { width:260px; max-width:100%; min-width:0; }
.automation-search :deep(.n-button) { flex-shrink:0; }
/* Keep panel-title typography out of Naive search controls. */
.automation-search :deep(span) { font-size:inherit; font-weight:inherit; color:inherit; }

.configuration-form, .automation-form, .configuration-grid, .drawer-footer { display:grid; gap:var(--space-4); }
.configuration-notice, .automation-switches {
  display:flex; align-items:center; gap:var(--space-2); padding:var(--space-3); border-radius:var(--card-radius);
  background:rgb(var(--primary-color) / .08); color:var(--text-color-2); font-size:var(--font-auxiliary);
}
.configuration-notice .app-icon { flex:0 0 auto; }
.configuration-card { min-width:0; margin:0; padding:0; border:0; }
.configuration-card legend { margin-bottom:var(--space-3); padding:0; font-size:var(--font-section-title); font-weight:650; }
.smtp-configuration-card { padding-top:var(--space-3); border-top:1px solid var(--theme-divider-color); }
.setting-switch {
  display:flex; align-items:center; gap:var(--space-2); width:100%; margin-bottom:var(--space-3); padding:var(--space-3);
  border:1px solid var(--theme-border-color); border-radius:var(--card-radius); background:var(--theme-hover-color);
}
.setting-switch strong, .setting-switch small { display:block; font-size:var(--font-auxiliary); }
.setting-switch small { margin-top:var(--space-1); color:var(--text-color-3); }
.setting-fields, .automation-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:var(--space-3); }
.setting-fields .span-two, .automation-form .wide { grid-column:1 / -1; }
.setting-fields label, .automation-fields > label { display:grid; align-content:start; min-width:0; gap:var(--space-1); }
.setting-fields label > span, .automation-fields > label > span, .recipient-heading {
  color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:650;
}
.setting-fields small, .automation-form small, .security-options small, .drawer-actions > span {
  color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.5; overflow-wrap:anywhere;
}
.security-options {
  display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2) var(--space-3); margin-top:var(--space-3);
  padding-top:var(--space-3); border-top:1px solid var(--theme-divider-color);
  color:var(--text-color-2); font-size:var(--font-auxiliary);
}
.security-options > span { font-weight:650; }
.security-options small { flex-basis:100%; }
.drawer-footer { width:100%; min-width:0; gap:var(--space-2); }
.drawer-actions, .drawer-actions > div { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); }
.drawer-actions { justify-content:space-between; }
.drawer-actions > span { flex:1 1 180px; }
.drawer-actions .n-button { min-height:36px; }
.drawer-actions > .n-button, .drawer-actions > div { margin-inline-start:auto; }
.recipient-field { min-width:0; }
.recipient-heading { display:flex; justify-content:space-between; margin-bottom:var(--space-2); }
.recipient-heading > span { color:var(--text-color-3); font-weight:400; }
.recipient-tags { display:flex; align-items:center; flex-wrap:wrap; gap:var(--space-2); max-height:160px; overflow-y:auto; padding:var(--space-1); }
.recipient-tags :deep(.n-tag) { max-width:100%; }
.recipient-tags :deep(.n-tag__content) { min-width:0; }
.recipient-address { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.recipient-entry { display:flex; flex-wrap:wrap; gap:var(--space-2); margin-top:var(--space-2); }
.recipient-entry :deep(.n-input) { flex:1 1 200px; min-width:0; }
.recipient-error { margin:var(--space-2) 0 0; color:rgb(var(--error-color)); font-size:var(--font-auxiliary); }
.automation-switches { flex-wrap:wrap; gap:var(--space-3); }
.configuration-load-error {
  display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:var(--space-3); padding:var(--space-4);
  color:rgb(var(--error-color)); font-size:var(--font-body);
}
.automation-empty, .automation-page .empty { padding:var(--space-6) var(--space-4); color:var(--text-color-3); font-size:var(--font-auxiliary); text-align:center; }
@media (max-width: 760px) {
  .automation-header { padding:var(--space-4); }
  .automation-overview { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .automation-list-tools, .automation-search { width:100%; }
  .automation-list-tools { flex-wrap:wrap; }
  .automation-search :deep(.n-input) { flex:1; width:auto; }
  .automation-page .panel-title { align-items:flex-start; flex-direction:column; }
  .automation-page .panel-title p { text-align:left; }
}
@media (max-width: 500px) {
  .automation-toolbar { width:100%; }
  .automation-toolbar > * { flex:1; }
  .setting-fields, .automation-fields { grid-template-columns:minmax(0,1fr); }
  .drawer-actions > span { flex-basis:100%; }
}
</style>
