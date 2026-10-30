<script setup lang="ts">
import { computed } from 'vue'
import { formatChineseDateTime as time } from '../../utils/dateDisplay'
import type { User } from '../../types/auth'
import type { WorkshopCorrectionState } from '../../composables/useWorkshopCorrection'

const props = defineProps<{ correction: WorkshopCorrectionState; isWorker: boolean; availableWorkers: User[]; visibleCorrectionReasonPresets: string[] }>()
const correctionDialogOpen = computed(() => props.correction.visible)
const action = defineModel<'revoke' | 'reassign'>('action', { required: true })
const workerId = defineModel<string>('workerId', { required: true })
const reason = defineModel<string>('reason', { required: true })
const emit = defineEmits<{ close: []; submit: []; reason: [reason: string] }>()

function display(value: string | null | undefined) { return value || '--' }
</script>

<template>
  <NModal :show="correctionDialogOpen" :mask-closable="!correction.saving" :close-on-esc="!correction.saving" @update:show="visible => { if (!visible) emit('close') }">
      <div class="confirmation-dialog" role="dialog" aria-modal="true" aria-labelledby="confirmation-dialog-title" tabindex="-1">
        <header><div><small>{{ isWorker ? '本人撤销' : '管理员更正' }}</small><h2 id="confirmation-dialog-title">{{ isWorker ? '撤销任务确认' : '更正确认人' }}</h2></div><NButton attr-type="button" aria-label="关闭" :disabled="correction.saving" @click="emit('close')">×</NButton></header>
        <div class="confirmation-dialog-task"><strong>{{ display(correction.row?.program_no) }}</strong><span>当前确认人：{{ correction.row?.confirmed_by }} · {{ time(correction.row?.confirmed_at) }}</span></div>
        <div v-if="correction.loading" class="confirmation-dialog-loading"><span class="spinner dark"></span>正在读取工人和审计记录…</div>
        <template v-else>
          <label v-if="!isWorker" class="confirmation-dialog-field"><span>更正方式</span><AppSelect v-model="action"><option value="revoke">撤销为待确认</option><option value="reassign">直接改派给其他工人</option></AppSelect></label>
          <label v-if="!isWorker && action === 'reassign'" class="confirmation-dialog-field"><span>目标工人</span><AppSelect v-model="workerId" required><option value="" disabled>请选择工人</option><option v-for="worker in availableWorkers" :key="worker.user_id" :value="worker.user_id">{{ worker.display_name }}{{ worker.company_no || worker.username ? ` · ${worker.company_no || worker.username}` : '' }}</option></AppSelect><small v-if="!availableWorkers.length">只显示已登录过系统的可用工人账号。</small></label>
          <div class="confirmation-reason-presets" role="group" aria-label="常用更正原因">
            <span>常用原因</span>
            <div><NButton v-for="preset in visibleCorrectionReasonPresets" :key="preset" attr-type="button" :class="{ active: reason === preset }" :type="reason === preset ? 'primary' : 'default'" round size="small" :disabled="correction.saving" @click="emit('reason', preset)">{{ preset }}</NButton></div>
          </div>
          <label class="confirmation-dialog-field"><span>更正原因</span><NInput v-model:value="reason" name="correction_reason" autocomplete="off" :maxlength="200" placeholder="选择常用原因，或直接填写其他原因…" required type="textarea" show-count :autosize="{ minRows: 3, maxRows: 8 }" :input-props="{ 'name': 'correction_reason', 'autocomplete': 'off', 'required': true }" /></label>
          <p v-if="correction.error" class="confirmation-dialog-error" role="alert">{{ correction.error }}</p>
          <section v-if="!isWorker" class="confirmation-audit-list"><h3>该任务的确认记录</h3><p v-if="!correction.audits.length">暂无历史更正记录</p><article v-for="audit in correction.audits" :key="audit.audit_id"><strong>{{ audit.action_label }}</strong><span>{{ audit.previous_worker_name || '待确认' }} → {{ audit.new_worker_name || '待确认' }}</span><small>{{ audit.operated_by_name }} · {{ time(audit.operated_at) }} · {{ audit.reason }}</small></article></section>
        </template>
        <footer><NButton attr-type="button" :disabled="correction.saving" @click="emit('close')">取消</NButton><NButton class="primary" attr-type="button" :disabled="correction.loading || correction.saving || (!isWorker && action === 'reassign' && !workerId)" @click="emit('submit')" type="primary" :loading="correction.saving">{{ correction.saving ? '保存中…' : isWorker ? '确认撤销' : '保存更正' }}</NButton></footer>
      </div>
    </NModal>
</template>
