<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NProgress } from 'naive-ui'
import type { AutomationExecution } from '../../types/automation'

const props = defineProps<{ execution: AutomationExecution; cancellable?: boolean }>()
const emit = defineEmits<{ cancel: [] }>()
const stages: Partial<Record<string, { label: string; completed: number }>> = {
  submitting: { label: '正在提交任务…', completed: 0 },
  queued: { label: '已提交，等待后台准备报表…', completed: 1 },
  generating: { label: '正在生成报表…', completed: 1 },
  analysis_wait: { label: '等待智能分析成功，后台将自动重试…', completed: 1 },
  email_pending: { label: '报表已生成，等待发送邮件…', completed: 2 },
  sending: { label: '正在发送邮件…', completed: 2 },
  retry_wait: { label: '发送失败，等待自动重试…', completed: 2 },
  sent: { label: 'SMTP 已接收', completed: 3 },
  cancelled: { label: '已取消发送', completed: 0 },
  failed: { label: '执行失败', completed: 0 },
  unknown: { label: '发送结果待核对，请在邮件发送记录中处理', completed: 2 },
  submission_failed: { label: '提交结果未确认', completed: 0 },
}
const stage = computed(() => stages[props.execution.status] || { label: '正在读取执行进度…', completed: 0 })
const progressStatus = computed(() => ['failed', 'submission_failed'].includes(props.execution.status) ? 'error'
  : ['unknown', 'retry_wait', 'analysis_wait'].includes(props.execution.status) ? 'warning'
    : props.execution.status === 'sent' ? 'success' : 'default')
const processing = computed(() => !['sent', 'failed', 'unknown', 'cancelled', 'submission_failed'].includes(props.execution.status))
</script>

<template>
  <section class="execution-progress" :aria-label="`${execution.schedule_name}执行进度`" :aria-busy="processing">
    <div class="execution-heading">
      <strong role="status" aria-live="polite">{{ stage.label }}</strong>
      <NButton v-if="cancellable" size="tiny" :loading="execution.cancelling" :disabled="execution.cancelling" @click="emit('cancel')">取消发送</NButton>
    </div>
    <template v-if="processing">
      <NProgress type="line" :percentage="stage.completed / 3 * 100" :show-indicator="false" :processing="processing" :status="progressStatus" :height="6" aria-label="任务阶段进度" :aria-valuetext="stage.label" />
      <div class="execution-steps"><span>提交任务</span><span>生成报表</span><span>发送邮件</span></div>
    </template>
    <p v-if="execution.last_error" class="execution-error" role="alert">{{ execution.last_error }}</p>
    <p v-if="execution.monitoring_error" class="execution-error" role="alert">{{ execution.monitoring_error }}</p>
  </section>
</template>

<style scoped>
.execution-progress { min-width:0; display:grid; gap:var(--space-2); padding:var(--space-2) var(--space-3); border-radius:var(--card-radius); background:var(--theme-hover-color); }
.execution-heading { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:var(--space-2); color:var(--text-color-1); font-size:var(--font-body); overflow-wrap:anywhere; }
.execution-steps { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.execution-steps { display:flex; justify-content:space-between; gap:var(--space-2); }
.execution-error { margin:8px 0 0; color:rgb(var(--error-color)); font-size:var(--font-auxiliary); overflow-wrap:anywhere; }
</style>
