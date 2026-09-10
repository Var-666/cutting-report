<script setup>
import { computed } from 'vue'
import DownloadPdfButton from './DownloadPdfButton.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  loading: Boolean,
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 10 },
  total: { type: Number, default: 0 },
})
const emit = defineEmits(['page-change', 'page-size-change'])

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
const visiblePages = computed(() => {
  const start = Math.max(1, Math.min(props.page - 2, totalPages.value - 4))
  const end = Math.min(totalPages.value, start + 4)
  return Array.from({ length: end - start + 1 }, (_, index) => start + index)
})
const rangeStart = computed(() => props.total ? (props.page - 1) * props.pageSize + 1 : 0)
const rangeEnd = computed(() => Math.min(props.page * props.pageSize, props.total))

const statusText = {
  pending: '等待中',
  querying: '查询中',
  calculating: '统计中',
  analyzing: '分析中',
  rendering: '渲染中',
  completed: '已完成',
  completed_with_warnings: '已完成（有提示）',
  failed: '失败',
}

function display(value) {
  return value || '全部'
}

function reportPeriod(item) {
  const start = item.period_start || item.report_date
  const end = item.period_end || item.report_date
  if (start && end && start !== end) return `${start} 至 ${end}`
  return start || '--'
}

const reportTypeText = {
  leadership: '领导报表',
  workshop: '车间报表',
  equipment: '设备报表',
  custom: '自定义报表',
}
</script>

<template>
  <div class="history-wrap">
    <table v-content-table class="history-table">
      <thead><tr><th>报告类型</th><th>报告周期</th><th>工程号</th><th>船号</th><th>任务数</th><th>生成状态</th><th>生成时间</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-if="loading"><td colspan="8" class="table-empty">正在加载...</td></tr>
        <tr v-else-if="!items.length"><td colspan="8" class="table-empty">暂无历史报告</td></tr>
        <tr v-for="item in items" v-else :key="item.report_id">
          <td>{{ item.report_title || reportTypeText[item.report_type] || '车间报表' }}</td>
          <td>{{ reportPeriod(item) }}</td>
          <td>{{ display(item.project_code) }}</td>
          <td>{{ display(item.ship_no) }}</td>
          <td>{{ item.task_count ?? '--' }}</td>
          <td><span class="mini-status" :data-status="item.status">{{ statusText[item.status] || item.status }}</span></td>
          <td>{{ item.generated_at || item.created_at }}</td>
          <td class="actions-cell">
            <a v-if="item.view_url" :href="item.view_url" target="_blank" rel="noopener">打开</a>
            <a v-if="item.download_url" :href="item.download_url">下载 HTML</a>
            <DownloadPdfButton v-if="item.download_url" :report-id="item.report_id" compact />
            <span v-if="!item.view_url">--</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="history-pagination" aria-label="报告列表分页">
    <div class="history-page-summary">共 {{ total }} 条，当前 {{ rangeStart }}–{{ rangeEnd }} 条</div>
    <label class="history-page-size">
      <span>每页</span>
      <AppSelect :value="pageSize" :disabled="loading" @change="emit('page-size-change', $event.target.value)">
        <option :value="10">10 条</option>
        <option :value="20">20 条</option>
        <option :value="50">50 条</option>
      </AppSelect>
    </label>
    <div class="history-page-buttons">
      <button type="button" :disabled="loading || page <= 1" @click="emit('page-change', page - 1)">上一页</button>
      <button
        v-for="pageNumber in visiblePages"
        :key="pageNumber"
        type="button"
        :class="{ active: pageNumber === page }"
        :aria-current="pageNumber === page ? 'page' : undefined"
        :disabled="loading"
        @click="emit('page-change', pageNumber)"
      >{{ pageNumber }}</button>
      <button type="button" :disabled="loading || page >= totalPages" @click="emit('page-change', page + 1)">下一页</button>
    </div>
  </div>
</template>
