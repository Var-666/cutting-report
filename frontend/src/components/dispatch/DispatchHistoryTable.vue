<script setup lang="ts">
import type { CuttingDispatch, CuttingProject } from '../../types/cutting'
import { NTag } from 'naive-ui'
import AppReportTable from '../AppReportTable.vue'
import { parseCuttingInstructionFilename } from '../../utils/cuttingInstructionName'
import { formatChineseDateTime as formatTime } from '../../utils/dateDisplay'

defineProps<{
  histories: CuttingDispatch[]
  historyTotal: number
  historyPage: number
  historyPageSize: number
  loading: boolean
  projectOptions: CuttingProject[]
  machineName: (ip: string) => string
}>()
const emit = defineEmits<{ page: [page: number] }>()

const historyTableColumns = [
  { key: 'c0', title: "船号", width: 140, align: 'left', fixed: 'left' },
  { key: 'c1', title: "分段号", width: 104, align: 'left' },
  { key: 'c2', title: "文件名", minWidth: 220, align: 'left' },
  { key: 'c3', title: "数量", width: 80, align: 'right' },
  { key: 'c4', title: "目标机床", width: 144, align: 'left' },
  { key: 'c5', title: "状态", width: 112, align: 'center' },
  { key: 'c6', title: "阶段", width: 144, align: 'left' },
  { key: 'c7', title: "任务标识", width: 200, align: 'left' },
  { key: 'c8', title: "魔盒UUID", width: 260, align: 'left' },
  { key: 'c9', title: "失败原因", width: 240, align: 'left' },
  { key: 'c10', title: "创建时间", width: 184, align: 'left' }
] as const
</script>

<template>
  <AppReportTable :columns="historyTableColumns" :items="histories" :total="historyTotal" :page="historyPage" :page-size="historyPageSize" :page-sizes="[]" @page="emit('page', $event)" :row-key="item => item.dispatch_id" :loading="loading">
        <template #c0="{ row: item }">{{ item.ship_no || projectOptions.find(project => project.project_id === item.project_id)?.ship_no || '--' }}</template>
        <template #c1="{ row: item }">{{ item.segment_name || parseCuttingInstructionFilename(item.document_no)?.intermediateProduct || '--' }}</template>
        <template #c2="{ row: item }">{{ item.document_no }}</template>
        <template #c3="{ row: item }">{{ item.quantity }}</template>
        <template #c4="{ row: item }">{{ machineName(item.machine_ip) }}</template>
        <template #c5="{ row: item }"><NTag class="dispatch-status" :class="item.status" size="small" :type="item.status === 'failed' ? 'error' : 'info'">{{ item.status_label }}</NTag></template>
        <template #c6="{ row: item }">{{ item.stage_label }}</template>
        <template #c7="{ row: item }"><code>{{ item.task_identifier || item.dispatch_id }}</code></template>
        <template #c8="{ row: item }"><code>{{ item.remote_task_id || '--' }}</code></template>
        <template #c9="{ row: item }"><span class="history-error">{{ item.last_error || '--' }}</span></template>
        <template #c10="{ row: item }">{{ formatTime(item.created_at) }}</template>
      </AppReportTable>
</template>

<style scoped>
.history-error { max-width:220px; color:rgb(var(--error-color)); overflow-wrap:anywhere; }
</style>
