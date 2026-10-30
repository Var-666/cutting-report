<script setup lang="ts">
import { NTag } from 'naive-ui'
import AppReportTable from '../AppReportTable.vue'
import { formatChineseDateTime as formatDateTime } from '../../utils/dateDisplay'
import type { AutomationSchedule } from '../../types/automation'
import { formatDeviceDisplay } from '../../utils/deviceDisplay'

defineProps<{ schedules: AutomationSchedule[]; loading: boolean; reportLabels: Record<string,string>; scheduleLabels: Record<string,string> }>()
const emit = defineEmits<{ run: [item: AutomationSchedule]; edit: [item: AutomationSchedule]; remove: [item: AutomationSchedule] }>()

function scheduleText(item: AutomationSchedule) {
  if (item.frequency === 'weekly') return `每周${'一二三四五六日'[(item.weekday || 1) - 1]} ${item.run_time}`
  if (item.frequency === 'monthly') return `每月 ${item.month_day} 日 ${item.run_time}`
  return `每日 ${item.run_time}`
}
const scheduleTableColumns = [
  { key: 'c0', title: '状态', width: 88, align: 'center' },
  { key: 'c1', title: '任务', minWidth: 280, align: 'left' },
  { key: 'c2', title: '报表', width: 112, align: 'left' },
  { key: 'c3', title: '计划', width: 160, align: 'left' },
  { key: 'c4', title: '下次执行', width: 184, align: 'left' },
  { key: 'c5', title: '收件人', width: 96, align: 'right' },
  { key: 'c6', title: '发送 / 失败', width: 120, align: 'right' },
  { key: 'c7', title: '操作', width: 248, align: 'center', fixed: 'right', ellipsis: false },
] as const
</script>

<template>
  <AppReportTable :columns="scheduleTableColumns" :items="schedules" :row-key="item => item.schedule_id" :paginate="false" :max-height="340" :loading="loading" aria-label="定时任务列表">
          <template #c0="{ row: item }"><NTag class="schedule-status" :class="{ enabled: item.enabled }" size="small" :type="item.enabled ? 'success' : 'default'">{{ item.enabled ? '启用' : '停用' }}</NTag></template>
          <template #c1="{ row: item }"><div class="task-cell"><strong>{{ item.name }}</strong><small>{{ formatDeviceDisplay(item.device_id) }}</small><small>{{ scheduleLabels[item.last_status] || item.last_status || '尚未执行' }}</small></div></template>
          <template #c2="{ row: item }">{{ reportLabels[item.report_type] }}</template>
          <template #c3="{ row: item }">{{ scheduleText(item) }}</template>
          <template #c4="{ row: item }">{{ formatDateTime(item.next_run_at) }}</template>
          <template #c5="{ row: item }"><span :title="item.recipients.join(', ')">{{ item.recipients.length }} 人</span></template>
          <template #c6="{ row: item }">{{ item.sent_count || 0 }} / {{ item.failed_count || 0 }}</template>
          <template #c7="{ row: item }"><div class="row-actions"><NButton attr-type="button" @click="emit('run', item)">立即执行</NButton><NButton attr-type="button" @click="emit('edit', item)">编辑</NButton><NButton attr-type="button" class="danger" @click="emit('remove', item)">删除</NButton></div></template>
          <template #empty>尚未创建定时任务，可在上方设置并创建。</template>
        </AppReportTable>
</template>

<style scoped src="./tableCells.css"></style>
