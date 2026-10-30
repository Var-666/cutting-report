<script setup lang="ts">
import { NTag } from 'naive-ui'
import AppReportTable from '../AppReportTable.vue'
import { formatChineseDateTime as formatDateTime } from '../../utils/dateDisplay'
import type { EmailDelivery } from '../../types/automation'
import { formatChineseDate } from '../../utils/dateDisplay'

defineProps<{ deliveries: EmailDelivery[]; deliveryTotal: number; deliveryPage: number; deliveryLoading: boolean; paginate: boolean; deliveryLabels: Record<string,string> }>()
const emit = defineEmits<{ page: [page: number]; retry: [item: EmailDelivery]; open: [item: EmailDelivery] }>()


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
  <AppReportTable :columns="deliveryTableColumns" :items="deliveries" :row-key="item => item.delivery_id" :paginate="paginate" :total="deliveryTotal" :page="deliveryPage" :page-size="20" :page-sizes="[]" :max-height="440" :loading="deliveryLoading" aria-label="邮件发送记录" @page="emit('page', $event)">
          <template #c0="{ row: item }">{{ formatDateTime(item.created_at) }}</template>
          <template #c1="{ row: item }"><div class="task-cell"><strong>{{ item.schedule_name }}</strong><small :title="item.subject">{{ item.subject }}</small></div></template>
          <template #c2="{ row: item }">{{ formatChineseDate(item.report_date) }}</template>
          <template #c3="{ row: item }"><div class="recipient-cell"><span>{{ item.recipients.join(', ') }}</span><small v-for="(result, address) in item.recipient_results" :key="address" :title="`${address}：${result.status}`">{{ address }}：{{ result.status === 'accepted' ? '服务器已接受' : result.status === 'unknown' ? '结果待核对' : result.status === 'not_accepted' ? '服务器未接收（人工核对）' : '服务器拒收' }}{{ result.code ? `（${result.code}）` : '' }}</small></div></template>
          <template #c4="{ row: item }"><NTag class="delivery-status" :class="item.status" size="small" :type="item.status === 'sent' ? 'success' : item.status === 'failed' ? 'error' : item.status === 'unknown' ? 'warning' : 'info'">{{ deliveryLabels[item.status] || item.status }}</NTag></template>
          <template #c5="{ row: item }">{{ item.attempt_count }} / {{ item.max_attempts }}</template>
          <template #c6="{ row: item }"><span class="error-cell" :title="item.last_error || ''">{{ item.sent_at ? formatDateTime(item.sent_at) : item.last_error || '—' }}</span></template>
          <template #c7="{ row: item }"><div class="delivery-actions"><NButton v-if="item.status === 'failed'" attr-type="button" @click="emit('retry', item)">重新发送</NButton><NButton attr-type="button" @click="emit('open', item)">{{ item.status === 'unknown' ? '核对结果' : '核对记录' }}</NButton><small v-if="item.status === 'unknown'" :title="item.message_id">{{ item.message_id || '无历史 Message-ID' }}</small></div></template>
          <template #empty>尚无邮件发送记录，任务执行后会显示在这里。</template>
        </AppReportTable>
</template>

<style scoped src="./tableCells.css"></style>
