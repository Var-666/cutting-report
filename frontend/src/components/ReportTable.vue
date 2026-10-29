<script setup lang="ts">
import { NTag } from 'naive-ui'
import AppReportTable from './AppReportTable.vue'
import type { PropType } from 'vue'
import type { ReportJob } from '../types/reports'

import DownloadPdfButton from './DownloadPdfButton.vue'
import { formatChineseDate, formatChineseDateTime, formatChineseMonth } from '../utils/dateDisplay.ts'

const props = defineProps({
  items: { type: Array as PropType<ReportJob[]>, default: () => [] },
  loading: Boolean,
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
  total: { type: Number, default: 0 },
})
const emit = defineEmits<{ 'page-change': [page: number]; 'page-size-change': [size: string | number] }>()


const statusText = {
  pending: '等待中',
  querying: '查询中',
  calculating: '统计中',
  analyzing: '分析中',
  rendering: '渲染中',
  completed: '已完成',
  failed: '失败',
}

function display(value: unknown) {
  return value || '全部'
}

function reportPeriod(item: ReportJob) {
  const start = item.period_start || item.report_date
  const end = item.period_end || item.report_date
  if (start && end && start !== end) return `${formatChineseDate(start)} 至 ${formatChineseDate(end)}`
  if (item.report_type === 'leadership') return formatChineseMonth(start)
  return formatChineseDate(start)
}

const reportTypeText = {
  leadership: '领导报表',
  workshop: '车间报表',
  equipment: '设备报表',
  custom: '自定义报表',
}

const reportTableColumns = [
  { key: 'c0', title: "报告类型", width: 104, align: 'left', fixed: 'left' },
  { key: 'c1', title: "报告周期", width: 220, align: 'left' },
  { key: 'c2', title: "工程号", width: 88, align: 'left' },
  { key: 'c3', title: "船号", width: 88, align: 'left' },
  { key: 'c4', title: "任务数", width: 84, align: 'right' },
  { key: 'c5', title: "生成状态", width: 104, align: 'center' },
  { key: 'c6', title: "生成时间", width: 184, align: 'left' },
  { key: 'c7', title: "操作", width: 248, align: 'center', fixed: 'right', ellipsis: false }
] as const
</script>

<template>
  <div class="history-wrap">
    <AppReportTable :columns="reportTableColumns" :items="items" :total="total" :page="page" :page-size="pageSize" :page-sizes="[10, 20, 50]" @page="emit('page-change', $event)" @page-size="emit('page-size-change', $event)" :row-key="item => item.report_id" :loading="loading">
        <template #c0="{ row: item }">{{ item.report_title || reportTypeText[item.report_type] || '车间报表' }}</template>
        <template #c1="{ row: item }">{{ reportPeriod(item) }}</template>
        <template #c2="{ row: item }">{{ display(item.project_code) }}</template>
        <template #c3="{ row: item }">{{ display(item.ship_no) }}</template>
        <template #c4="{ row: item }">{{ item.task_count ?? '--' }}</template>
        <template #c5="{ row: item }"><NTag class="mini-status" :data-status="item.status" size="small" :type="item.status === 'completed' ? 'success' : item.status === 'failed' ? 'error' : 'info'">{{ statusText[item.status] || item.status }}</NTag></template>
        <template #c6="{ row: item }">{{ formatChineseDateTime(item.generated_at || item.created_at) }}</template>
        <template #c7="{ row: item }"><div class="actions-cell"><a v-if="item.view_url" :href="item.view_url" target="_blank" rel="noopener">打开</a>
            <a v-if="item.download_url" :href="item.download_url">下载 HTML</a>
            <DownloadPdfButton v-if="item.download_url" :report-id="item.report_id" compact />
            <span v-if="!item.view_url">--</span></div></template>
        <template #empty><span class="history-empty">{{ loading ? '正在加载…' : '暂无历史报告' }}</span></template>
      </AppReportTable>
  </div>
</template>

<style scoped>
.history-wrap { min-width:0; padding:16px; }
.history-wrap :deep(.app-report-pagination) { padding-bottom:0; }
.history-empty { color:var(--text-color-3); }
.actions-cell { display:flex; align-items:center; justify-content:center; gap:12px; white-space:nowrap; }
.actions-cell a { margin-right:0; }
</style>
