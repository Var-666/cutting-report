<script setup lang="ts">
import { NButton, NEmpty, NTag } from 'naive-ui'
import AppIcon from '../AppIcon.vue'
import AutomationRunProgress from './AutomationRunProgress.vue'
import { formatChineseDateTime as formatDateTime } from '../../utils/dateDisplay'
import type { AutomationExecution, AutomationSchedule } from '../../types/automation'
import { formatDeviceDisplay } from '../../utils/deviceDisplay'

defineProps<{
  schedules: AutomationSchedule[]; searching?: boolean; runningScheduleIds?: string[]; cancellableScheduleIds?: string[]
  updatingScheduleIds?: ReadonlySet<string>
  executions?: Record<string, AutomationExecution>; reportLabels: Record<string, string>; scheduleLabels: Record<string, string>
}>()
const emit = defineEmits<{
  run: [item: AutomationSchedule]; cancel: [execution: AutomationExecution]; edit: [item: AutomationSchedule]
  toggle: [item: AutomationSchedule]; remove: [item: AutomationSchedule]
}>()

function scheduleText(item: AutomationSchedule) {
  if (item.frequency === 'weekly') return `每周${'一二三四五六日'[(item.weekday || 1) - 1]} ${item.run_time}`
  if (item.frequency === 'monthly') return `每月 ${String(item.month_day).padStart(2, '0')} 日 ${item.run_time}`
  return `每日 ${item.run_time}`
}
</script>

<template>
  <NEmpty v-if="!schedules.length" class="schedule-empty" :description="searching ? '未找到匹配的定时任务' : '尚未创建定时任务'" />
  <div v-else class="schedule-grid" aria-label="定时任务列表">
    <article v-for="item in schedules" :key="item.schedule_id" class="schedule-card" :class="{ 'schedule-card--disabled': !item.enabled }" :aria-label="item.name">
      <header class="card-heading">
        <span class="card-icon"><AppIcon name="calendar" :size="20" tone="current" /></span>
        <div class="card-title">
          <h3 :title="item.name">{{ item.name }}</h3>
          <p :title="`${reportLabels[item.report_type]} · ${formatDeviceDisplay(item.device_id)}`">{{ reportLabels[item.report_type] }} · {{ formatDeviceDisplay(item.device_id) }}</p>
        </div>
        <NTag size="small" class="card-state" :bordered="false" :type="item.enabled ? 'success' : 'default'">{{ item.enabled ? '启用' : '暂停' }}</NTag>
        <NButton size="small" type="error" secondary class="card-remove"
          :aria-label="`删除 ${item.name}`" :disabled="updatingScheduleIds?.has(item.schedule_id)" @click="emit('remove', item)">
          <span aria-hidden="true">×</span>
        </NButton>
      </header>
      <dl class="card-details">
        <div class="card-next">
          <dt>{{ item.enabled ? '下次执行' : '自动执行暂停' }}</dt><dd><time v-if="item.enabled" :datetime="item.next_run_at || undefined">{{ formatDateTime(item.next_run_at) }}</time>
            <p class="card-frequency" :aria-label="`执行计划：${scheduleText(item)}`"><AppIcon name="clock" :size="14" tone="current" />{{ scheduleText(item) }}</p>
          </dd>
        </div>
        <div><dt>收件人</dt><dd :title="item.recipients.join('、')">{{ item.recipients.length }}<small>人</small></dd></div>
        <div><dt>SMTP 已接收</dt><dd>{{ item.sent_count || 0 }}</dd></div>
        <div><dt>发送失败</dt><dd :class="{ 'card-failed': item.failed_count }">{{ item.failed_count || 0 }}</dd></div>
      </dl>
      <AutomationRunProgress v-if="executions?.[item.schedule_id]" :execution="executions[item.schedule_id]"
        :cancellable="cancellableScheduleIds?.includes(item.schedule_id)" @cancel="emit('cancel', executions[item.schedule_id])" />
      <p v-else class="last-status">{{ scheduleLabels[item.last_status || ''] || item.last_status || '尚未执行' }}</p>
      <footer class="card-actions">
        <NButton type="primary" :disabled="runningScheduleIds?.includes(item.schedule_id)" @click="emit('run', item)">
          {{ runningScheduleIds?.includes(item.schedule_id) ? '执行中' : '立即执行' }}
        </NButton>
        <NButton :disabled="updatingScheduleIds?.has(item.schedule_id)" @click="emit('edit', item)">编辑</NButton>
        <NButton secondary :loading="updatingScheduleIds?.has(item.schedule_id)" :disabled="updatingScheduleIds?.has(item.schedule_id)" @click="emit('toggle', item)">{{ item.enabled ? '暂停' : '恢复' }}</NButton>
      </footer>
    </article>
  </div>
</template>

<style scoped>
.schedule-grid { display:flex; flex-wrap:wrap; align-items:stretch; gap:var(--space-4); }
.schedule-card { --schedule-accent:rgb(var(--primary-color)); display:flex; flex:1 1 360px; flex-direction:column; gap:var(--space-4); min-width:0; max-width:440px; padding:var(--space-4); border:1px solid var(--theme-border-color); border-radius:var(--card-radius); background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); container-type:inline-size; }
.schedule-card--disabled { --schedule-accent:var(--text-color-3); }
.schedule-card:focus-within { border-color:color-mix(in srgb,var(--schedule-accent) 55%,var(--theme-border-color)); }
.card-heading, .card-actions { display:flex; align-items:center; gap:var(--space-2); }
.card-state, .card-remove { flex:none; justify-content:center; width:36px; height:var(--space-6); padding:0; border-radius:var(--control-radius); }
.card-remove { border:1px solid rgb(var(--error-color) / .24); }
.card-remove span { font-size:18px; line-height:1; }
.card-icon { display:grid; place-items:center; width:36px; height:36px; flex:none; color:var(--schedule-accent); background:color-mix(in srgb,var(--schedule-accent) 11%,transparent); border-radius:var(--card-radius); }
.card-title { flex:1; min-width:0; }
.card-title h3, .card-title p { margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.card-title h3 { font-size:var(--font-section-title); line-height:1.4; font-weight:650; color:var(--text-color-1); }
.card-title p { margin-top:var(--space-1); }
.card-title p, .card-details dt, .last-status { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.card-details { display:grid; grid-template-columns:repeat(3,minmax(0, 1fr)); gap:var(--space-4) var(--space-3); margin:0; }
.card-details > div { min-width:0; }
.card-details dd { margin:var(--space-1) 0 0; color:var(--text-color-1); font-size:var(--font-kpi-value); font-weight:600; line-height:1.3; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.card-details dd small { margin-left:var(--space-1); font-size:var(--font-auxiliary); font-weight:400; color:var(--text-color-3); }
.card-details > div:last-child { padding-left:var(--space-3); border-left:1px solid var(--theme-divider-color); }
.card-details .card-next { grid-column:1/-1; padding:var(--space-3); border:1px solid color-mix(in srgb,var(--schedule-accent) 12%,transparent); border-radius:var(--card-radius); background:linear-gradient(100deg,color-mix(in srgb,var(--schedule-accent) 10%,transparent),color-mix(in srgb,var(--schedule-accent) 3.5%,transparent)); }
.card-next dd { margin-top:var(--space-2); font-size:clamp(var(--font-section-title),5cqi,20px); }
.card-frequency { display:flex; align-items:center; gap:var(--space-1); margin:var(--space-2) 0 0; font-size:var(--font-auxiliary); font-weight:400; color:var(--text-color-2); }
.card-details dd.card-failed { color:rgb(var(--error-color)); }
.last-status { margin:0; }
.card-actions { justify-content:flex-end; flex-wrap:wrap; margin-top:auto; padding:var(--space-3) 0 0; border-top:1px solid var(--theme-divider-color); }
.card-actions > :first-child { margin-right:auto; }
.schedule-empty { padding:var(--space-6); }
</style>
