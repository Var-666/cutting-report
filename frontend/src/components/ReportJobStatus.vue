<script setup lang="ts">
import type { PropType } from 'vue'
import type { ReportJob } from '../types/reports'

import DownloadPdfButton from './DownloadPdfButton.vue'
import { NSteps, NStep, NTag } from 'naive-ui'
defineProps({ job: { type: Object as PropType<ReportJob | null>, default: null } })

const steps = [
  ['querying', '查询数据'],
  ['calculating', '计算指标'],
  ['analyzing', '智能分析'],
  ['rendering', '生成报告'],
]
const rank: Record<string, number> = { pending: 0, querying: 1, calculating: 2, analyzing: 3, rendering: 4, completed: 5, failed: -1 }
const reportTypeText = { leadership: '领导报表', workshop: '车间报表', equipment: '设备报表', custom: '自定义报表' }
const aiStatusText: Record<string, string> = { success: '已生成', unavailable: '暂不可用', skipped: '未启用' }

</script>

<template>
  <section v-if="job" class="job-card" :class="{ failed: job.status === 'failed' }">
    <div class="job-heading">
      <div>
        <span class="eyebrow">当前生成任务 · {{ reportTypeText[job.report_type] || '报表' }}</span>
        <h2>{{ job.stage_message }}</h2>
      </div>
      <NTag class="status-pill" :data-status="job.status" :type="job.status === 'failed' ? 'error' : job.status === 'completed' ? 'success' : 'info'" size="small">{{ job.status }}</NTag>
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
.report-job-steps{margin:20px 0;flex-wrap:wrap;gap:12px}
</style>
