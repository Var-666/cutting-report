<script setup lang="ts">
import { NButton, NTag } from 'naive-ui'
import AppReportTable from '../AppReportTable.vue'
import { formatChineseDateTime as formatDateTime } from '../../utils/dateDisplay'
import type { EmailDelivery } from '../../types/automation'
import { formatChineseDate } from '../../utils/dateDisplay'

defineProps<{ deliveries: EmailDelivery[]; deliveryTotal: number; deliveryPage: number; deliveryLoading: boolean; paginate: boolean; searching?: boolean; deliveryLabels: Record<string,string> }>()
const emit = defineEmits<{ page: [page: number]; retry: [item: EmailDelivery]; open: [item: EmailDelivery] }>()

function recipientsText(item: EmailDelivery) {
  const labels: Record<string, string> = { accepted: '服务器已接受', unknown: '结果待核对', not_accepted: '服务器未接收（人工核对）', refused: '服务器拒收' }
  return [...new Set([...item.recipients, ...Object.keys(item.recipient_results || {})])].map(address => {
    const result = item.recipient_results?.[address]
    return result ? `${address}（${labels[result.status] || result.status}${result.code ? ` ${result.code}` : ''}）` : address
  }).join('；')
}

const deliveryTableColumns = [
  { key: 'c0', title: '创建时间', width: 184, align: 'left' },
  { key: 'c1', title: '任务', minWidth: 240, align: 'left' },
  { key: 'c2', title: '报表日期', width: 152, align: 'left' },
  { key: 'c3', title: '收件人', minWidth: 260, align: 'left' },
  { key: 'c4', title: '状态', width: 112, align: 'center' },
  { key: 'c5', title: '尝试', width: 96, align: 'right' },
  { key: 'c6', title: '结果', minWidth: 240, align: 'left' },
  { key: 'c7', title: '操作', width: 208, align: 'center', fixed: 'right', ellipsis: false },
] as const
</script>

<template>
  <AppReportTable :columns="deliveryTableColumns" :items="deliveries" :row-key="item => item.delivery_id" :paginate="paginate" :total="deliveryTotal" :page="deliveryPage" :page-size="20" :page-sizes="[]" :max-height="null" :loading="deliveryLoading" aria-label="邮件发送记录" @page="emit('page', $event)">
          <template #c0="{ row: item }">{{ formatDateTime(item.created_at) }}</template>
          <template #c1="{ row: item }"><strong :title="item.subject">{{ item.schedule_name }}</strong></template>
          <template #c2="{ row: item }">{{ formatChineseDate(item.report_date) }}</template>
          <template #c3="{ row: item }">{{ recipientsText(item) }}</template>
          <template #c4="{ row: item }"><NTag class="delivery-status" :class="item.status" size="small" :type="item.status === 'sent' ? 'success' : item.status === 'failed' ? 'error' : item.status === 'unknown' ? 'warning' : 'info'">{{ deliveryLabels[item.status] || item.status }}</NTag></template>
          <template #c5="{ row: item }">{{ item.attempt_count }} / {{ item.max_attempts }}</template>
          <template #c6="{ row: item }"><span class="error-cell" :title="item.last_error || ''">{{ item.sent_at ? formatDateTime(item.sent_at) : item.last_error || '—' }}</span></template>
          <template #c7="{ row: item }"><div class="delivery-actions"><NButton v-if="item.status === 'failed'" attr-type="button" @click="emit('retry', item)">重新发送</NButton><NButton attr-type="button" @click="emit('open', item)">{{ item.status === 'unknown' ? '核对结果' : '核对记录' }}</NButton></div></template>
          <template #empty>{{ searching ? '未找到匹配的邮件发送记录。' : '尚无邮件发送记录，任务执行后会显示在这里。' }}</template>
        </AppReportTable>
</template>

<style scoped src="./tableCells.css"></style>
