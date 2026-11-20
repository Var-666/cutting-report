<script setup lang="ts">
import type { PropType } from 'vue'
import type { ReportJobState } from '../types/reports'

import DownloadPdfButton from './DownloadPdfButton.vue'
import { NSteps, NStep, NTag } from 'naive-ui'
defineProps({ job: { type: Object as PropType<ReportJobState | null>, default: null } })

const steps = [
  ['querying', '查询数据'],
  ['calculating', '计算指标'],
  ['analyzing', '智能分析'],
  ['rendering', '生成报告'],
]
const rank: Record<string, number> = { pending: 0, querying: 1, calculating: 2, analyzing: 3, rendering: 4, completed: 5, failed: -1 }
const reportTypeText = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', custom: '自定义报表' }
const aiStatusText: Record<string, string> = { success: '已生成', unavailable: '暂不可用', skipped: '未启用' }
const statusText = { pending: '后台排队', querying: '查询中', calculating: '统计中', analyzing: '分析中',
  rendering: '生成 HTML', completed: 'HTML 已归档', failed: '失败' }

</script>

<template>
  <section v-if="job" class="job-card" :class="{ failed: job.status === 'failed' }">
    <div class="job-heading">
      <div>
        <small v-if="job.report_type" class="job-report-type">{{ reportTypeText[job.report_type] || job.report_type }}</small>
        <h2>{{ job.stage_message || '报告已进入后台队列' }}</h2>
      </div>
      <NTag class="status-pill" :data-status="job.status" :type="job.status === 'failed' ? 'error' : job.status === 'completed' ? 'success' : 'info'" size="small">{{ job.ai_status === 'waiting' ? '等待智能分析' : statusText[job.status] }}</NTag>
    </div>
    <NSteps v-if="job.status !== 'failed'" class="report-job-steps" size="small" :current="rank[job.status] || 0" :status="job.status === 'completed' ? 'finish' : 'process'">
      <NStep v-for="[status, label] in steps" :key="status" :title="label" />
    </NSteps>
    <p v-if="job.error" class="error-message">{{ job.error.message }}</p>
    <div v-if="job.status === 'completed'" class="result-actions">
      <NButton tag="a" type="primary" :href="job.view_url || undefined" target="_blank" rel="noopener">打开报告</NButton>
      <NButton tag="a" :href="job.download_url || undefined">下载 HTML</NButton>
      <DownloadPdfButton :report-id="job.report_id" />
      <span>{{ job.task_count }} 个任务 · AI {{ aiStatusText[job.ai_status || ''] || job.ai_status || '--' }}</span>
    </div>
  </section>
</template>

<style scoped>
.job-report-type{color:var(--text-color-3);font-size:var(--font-auxiliary)}
.report-job-steps{margin:20px 0;flex-wrap:wrap;gap:12px}
</style>
