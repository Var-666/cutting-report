<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  deleteAutomationSchedule,
  getAutomationStatus,
  listAutomationSchedules,
  listEmailDeliveries,
  retryEmailDelivery,
  runAutomationSchedule,
  saveAutomationSchedule,
} from '../api/reports'

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

function resetForm() {
  editingId.value = ''
  Object.assign(form, emptyForm())
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
  window.scrollTo({ top: 0, behavior: 'smooth' })
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
  return value ? value.replace('T', ' ').slice(0, 16) : '--'
}

onMounted(loadAll)
</script>

<template>
  <div class="automation-page">
    <section class="hero automation-hero">
      <div>
        <p class="eyebrow">REPORT AUTOMATION</p>
        <h2>报表自动化</h2>
        <p>定时生成报表，通过邮件队列发送，失败后按退避策略重试。</p>
      </div>
      <div class="smtp-card" :class="{ ready: status?.smtp?.configured }">
        <span>SMTP 服务</span>
        <strong>{{ status?.smtp?.configured ? '已就绪' : '待配置' }}</strong>
        <small>{{ status?.smtp?.configured ? `${status.smtp.host}:${status.smtp.port}` : '定时任务可保存，发送时会记录失败原因' }}</small>
      </div>
    </section>

    <div v-if="message" class="automation-message success">{{ message }}</div>
    <div v-if="error" class="automation-message error">{{ error }}</div>

    <section class="automation-kpis">
      <article><span>已启用计划</span><strong>{{ status?.summary?.schedule_enabled || 0 }}</strong></article>
      <article><span>等待/重试</span><strong>{{ status?.summary?.delivery_pending || 0 }}</strong></article>
      <article><span>已发送</span><strong>{{ status?.summary?.delivery_sent || 0 }}</strong></article>
      <article><span>最终失败</span><strong>{{ status?.summary?.delivery_failed || 0 }}</strong></article>
    </section>

    <section class="panel automation-editor">
      <div class="panel-title">
        <div><span>01</span><h2>{{ editingId ? '编辑定时任务' : '新建定时任务' }}</h2></div>
        <p>邮件发送独立重试，不会重复生成报表</p>
      </div>
      <form class="automation-form" @submit.prevent="submit">
        <label><span>任务名称</span><input v-model.trim="form.name" required maxlength="80"></label>
        <label><span>报表类型</span><select v-model="form.report_type"><option v-for="(label,key) in reportLabels" :key="key" :value="key">{{ label }}</option></select></label>
        <label><span>执行频率</span><select v-model="form.frequency"><option value="daily">每日</option><option value="weekly">每周</option><option value="monthly">每月</option></select></label>
        <label><span>执行时间</span><input v-model="form.run_time" type="time" required></label>
        <label v-if="form.frequency === 'weekly'"><span>星期</span><select v-model.number="form.weekday"><option v-for="(day,index) in ['一','二','三','四','五','六','日']" :key="day" :value="index + 1">星期{{ day }}</option></select></label>
        <label v-if="form.frequency === 'monthly'"><span>每月执行日</span><input v-model.number="form.month_day" type="number" min="1" max="28"></label>
        <label><span>报表期间</span><select v-model="form.report_period"><template v-if="form.report_type === 'leadership'"><option value="previous_month">上月</option><option value="current_month">本月</option></template><template v-else><option value="previous_day">前一日</option><option value="current_day">当日</option></template></select></label>
        <label><span>图表类型</span><select v-model="form.chart_type"><option value="bar_line">柱线组合</option><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option></select></label>
        <label><span>最大尝试次数</span><input v-model.number="form.max_attempts" type="number" min="1" max="10"></label>
        <label class="wide"><span>收件人（每行一个，或用逗号分隔）</span><textarea v-model="form.recipients_text" rows="3" required placeholder="manager@example.com"></textarea></label>
        <label class="wide"><span>邮件主题</span><input v-model.trim="form.subject_template" required><small>可用：{report_name}、{report_type}、{report_date}</small></label>
        <div class="automation-switches wide">
          <label><input v-model="form.attach_report" type="checkbox">附加 HTML 报表</label>
          <label><input v-model="form.enabled" type="checkbox">启用定时任务</label>
        </div>
        <div class="automation-actions wide">
          <button v-if="editingId" type="button" class="secondary" @click="resetForm">取消编辑</button>
          <button type="submit" :disabled="saving">{{ saving ? '保存中…' : editingId ? '保存修改' : '创建任务' }}</button>
        </div>
      </form>
    </section>

    <section class="panel automation-list">
      <div class="panel-title"><div><span>02</span><h2>定时任务</h2></div><p>{{ schedules.length }} 个计划</p></div>
      <div v-if="loading && !schedules.length" class="empty">正在读取定时任务…</div>
      <div v-else-if="!schedules.length" class="empty">尚未创建定时任务。</div>
      <div v-else class="automation-table-wrap">
        <table><thead><tr><th>状态</th><th>任务</th><th>报表</th><th>计划</th><th>下次执行</th><th>收件人</th><th>发送/失败</th><th>操作</th></tr></thead>
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
      <div class="panel-title"><div><span>03</span><h2>邮件发送记录</h2></div><p>可查看尝试次数、失败原因并手动重发</p></div>
      <div v-if="!deliveries.length" class="empty">尚无邮件发送记录。</div>
      <div v-else class="automation-table-wrap"><table><thead><tr><th>创建时间</th><th>任务</th><th>报表日期</th><th>收件人</th><th>状态</th><th>尝试</th><th>结果</th><th>操作</th></tr></thead>
        <tbody><tr v-for="item in deliveries" :key="item.delivery_id"><td>{{ formatDateTime(item.created_at) }}</td><td><strong>{{ item.schedule_name }}</strong><small>{{ item.subject }}</small></td><td>{{ item.report_date }}</td><td>{{ item.recipients.join(', ') }}</td><td><span class="delivery-status" :class="item.status">{{ deliveryLabels[item.status] || item.status }}</span></td><td>{{ item.attempt_count }}/{{ item.max_attempts }}</td><td class="error-cell" :title="item.last_error || ''">{{ item.sent_at ? formatDateTime(item.sent_at) : item.last_error || '--' }}</td><td><button v-if="item.status === 'failed'" type="button" @click="retry(item)">重新发送</button><span v-else>--</span></td></tr></tbody></table></div>
      <div class="automation-pagination"><span>第 {{ deliveryPage }} / {{ pageCount }} 页，共 {{ deliveryTotal }} 条</span><div><button type="button" :disabled="deliveryPage <= 1" @click="deliveryPage--; loadAll()">上一页</button><button type="button" :disabled="deliveryPage >= pageCount" @click="deliveryPage++; loadAll()">下一页</button></div></div>
    </section>
  </div>
</template>

<style scoped>
.automation-page{display:grid;gap:18px}.automation-hero{min-height:190px;display:flex;align-items:center;justify-content:space-between;gap:30px}.automation-hero h2{margin:7px 0 6px;font-size:34px}.automation-hero p:not(.eyebrow){margin:0;color:var(--muted)}.smtp-card{width:300px;padding:18px 20px;border:1px solid #e0c38d;border-left:5px solid #d89a27;background:#fff9ec}.smtp-card.ready{border-color:#9acdb2;border-left-color:#2c9961;background:#f0faf4}.smtp-card span,.smtp-card small{display:block;color:var(--muted);font-size:11px}.smtp-card strong{display:block;margin:5px 0;color:var(--navy);font-size:22px}.automation-message{padding:12px 15px;border-left:4px solid}.automation-message.success{color:#236a42;border-color:#2c9961;background:#eaf7ef}.automation-message.error{color:#9f302a;border-color:#c94a43;background:#fff0ef}.automation-kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}.automation-kpis article{padding:19px 22px;border-top:4px solid var(--cyan);background:#fff;box-shadow:var(--shadow)}.automation-kpis span{display:block;color:var(--muted);font-size:11px}.automation-kpis strong{display:block;margin-top:7px;color:var(--blue);font-size:28px}.automation-form{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;padding:24px}.automation-form label>span{display:block;margin-bottom:6px;color:#425767;font-size:11px;font-weight:700}.automation-form textarea{width:100%;resize:vertical;padding:10px 12px;border:1px solid #bdcbd5;background:#fbfcfd}.automation-form small{display:block;margin-top:5px;color:var(--muted);font-size:10px}.automation-form .wide{grid-column:1/-1}.automation-switches{display:flex;gap:18px}.automation-switches label{display:flex;align-items:center;gap:7px;color:#425767;font-size:11px}.automation-switches input{width:16px;height:16px}.automation-actions{display:flex;justify-content:flex-end;gap:9px;padding-top:8px;border-top:1px solid var(--line)}.automation-actions .secondary{color:var(--blue);background:#fff}.automation-table-wrap{overflow-x:auto}.automation-table-wrap table{width:100%;min-width:1050px;border-collapse:collapse}.automation-table-wrap th,.automation-table-wrap td{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left;font-size:11px}.automation-table-wrap th{color:#fff;background:var(--navy)}.automation-table-wrap td strong,.automation-table-wrap td small{display:block}.automation-table-wrap td small{margin-top:3px;max-width:240px;overflow:hidden;color:var(--muted);text-overflow:ellipsis;white-space:nowrap}.status-dot{width:8px;height:8px;display:inline-block;margin-right:6px;border-radius:50%;background:#aab7bf}.status-dot.enabled{background:#2c9961}.row-actions{display:flex;gap:5px;white-space:nowrap}.row-actions button,.automation-table-wrap td>button{min-height:29px;padding:0 8px;font-size:10px}.row-actions .danger{color:#a33a32;border-color:#dfb5b1;background:#fff7f6}.delivery-status{display:inline-block;padding:4px 7px;border-radius:999px;background:#eef2f4}.delivery-status.sent{color:#1f7148;background:#e9f7ef}.delivery-status.failed{color:#9f302a;background:#fff0ef}.delivery-status.retry_wait{color:#8b5a08;background:#fff5dc}.error-cell{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.automation-pagination{display:flex;align-items:center;justify-content:space-between;padding:13px 16px;color:var(--muted);font-size:11px}.automation-pagination div{display:flex;gap:7px}.automation-pagination button{min-height:31px;padding:0 10px}@media(max-width:900px){.automation-hero{align-items:flex-start;flex-direction:column;padding:28px 0}.smtp-card{width:100%}.automation-kpis{grid-template-columns:1fr 1fr}.automation-form{grid-template-columns:1fr 1fr}}@media(max-width:560px){.automation-kpis,.automation-form{grid-template-columns:1fr}.automation-form .wide{grid-column:auto}.automation-switches{align-items:flex-start;flex-direction:column}.automation-actions{align-items:stretch;flex-direction:column}}
</style>
