<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NTag } from 'naive-ui'
import AppReportTable from './AppReportTable.vue'
import type { DatabaseMonitor } from '../api/generated/types.gen'
import { formatChineseDateTime } from '../utils/dateDisplay'

const props = defineProps<{ items: DatabaseMonitor[]; loading: boolean; busy: boolean; now: number; unavailable: boolean; timezone: string }>()
defineEmits<{ edit: [item: DatabaseMonitor]; toggle: [item: DatabaseMonitor]; delete: [item: DatabaseMonitor] }>()
const labels = { healthy: '端口可达', suspect: '异常待确认', down: '连接不可用', recovering: '恢复确认中', unknown: '状态未知', paused: '已暂停' }
const rows = computed(() => props.items.map(item => ({ ...item, state: !item.enabled ? 'paused' as const
  : props.unavailable || item.last_checked_at === null || props.now - item.last_checked_at > 180 ? 'unknown' as const : item.state })))
const columns = [
  { key: 'c0', title: '名称', align: 'left' }, { key: 'c1', title: 'IP 地址', align: 'left' },
  { key: 'c2', title: '端口', align: 'left' }, { key: 'c3', title: '状态', align: 'center' },
  { key: 'c4', title: '最近检测', align: 'left' }, { key: 'c5', title: '故障开始', align: 'left' },
  { key: 'c6', title: '持续时长', align: 'right' }, { key: 'c7', title: '最近失败原因', align: 'left' },
  { key: 'c8', title: '操作', align: 'center', width: 200, fixed: 'right', ellipsis: false },
] as const
function stamp(value: number | null) { return value === null ? '—' : formatChineseDateTime(new Date(value * 1000).toLocaleString('sv-SE', { timeZone: props.timezone })) }
function duration(value: number | null) {
  if (value === null) return '—'
  const seconds = Math.max(0, Math.floor(props.now - value))
  return seconds < 60 ? `${seconds} 秒` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟` : `${Math.floor(seconds / 3600)} 小时 ${Math.floor(seconds % 3600 / 60)} 分钟`
}
</script>

<template>
  <AppReportTable :columns="columns" :items="rows" :loading="loading" :total="rows.length" :paginate="false" :page-sizes="[]" :row-key="item => item.monitor_id" aria-label="数据库端口监控">
    <template #c0="{ row }">{{ row.name }}</template>
    <template #c1="{ row }">{{ row.ip }}</template>
    <template #c2="{ row }">{{ row.port }}</template>
    <template #c3="{ row }"><NTag size="small" :type="row.state === 'healthy' ? 'success' : row.state === 'down' ? 'error' : ['suspect', 'recovering'].includes(row.state) ? 'warning' : 'default'">{{ labels[row.state] }}</NTag></template>
    <template #c4="{ row }">{{ stamp(row.last_checked_at) }}</template>
    <template #c5="{ row }">{{ stamp(row.failure_started_at) }}</template>
    <template #c6="{ row }">{{ duration(row.failure_started_at) }}</template>
    <template #c7="{ row }">{{ row.last_error || '—' }}</template>
    <template #c8="{ row }"><span class="monitor-actions">
      <NButton size="small" :disabled="busy" @click="$emit('edit', row)">编辑</NButton>
      <NButton size="small" :disabled="busy" @click="$emit('toggle', row)">{{ row.enabled ? '暂停' : '启用' }}</NButton>
      <NButton size="small" type="error" secondary :disabled="busy" @click="$emit('delete', row)">删除</NButton>
    </span></template>
    <template #empty>暂无监控目标，点击“添加监控”开始。</template>
  </AppReportTable>
</template>

<style scoped>
.monitor-actions { display:inline-flex; gap:8px; white-space:nowrap; }
</style>
