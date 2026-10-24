<script setup>
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { listEmailDeliveryResolutions, resolveEmailDelivery } from '../api/reports'
import { formatChineseDateTime } from '../utils/dateDisplay.js'

const props = defineProps({ delivery: { type: Object, required: true } })
const emit = defineEmits(['resolved', 'close', 'dirty'])
const outcomes = reactive({})
const evidence = ref('')
const saving = ref(false)
const resolved = ref(false)
const error = ref('')
const audits = ref([])
const auditLoading = ref(false)
const auditError = ref('')
const pendingRecipients = computed(() => props.delivery.recipients.filter(
  (address) => props.delivery.recipient_results?.[address]?.status !== 'accepted',
))
const dirty = computed(() => Boolean(evidence.value || Object.values(outcomes).some(Boolean)))
const canSubmit = computed(() => props.delivery.status === 'unknown' && props.delivery.message_id
  && pendingRecipients.value.length > 0 && evidence.value.trim()
  && pendingRecipients.value.every((address) => ['accepted', 'not_accepted'].includes(outcomes[address])))
watch(dirty, (value) => emit('dirty', value))
let active = true
let mounted = true
let auditController
let auditGeneration = 0

async function loadAudit() {
  const generation = ++auditGeneration
  auditController?.abort()
  auditController = new AbortController()
  auditLoading.value = true
  auditError.value = ''
  try {
    const response = await listEmailDeliveryResolutions(props.delivery.delivery_id, { signal: auditController.signal })
    if (active && generation === auditGeneration) audits.value = response.items || []
  } catch (exception) {
    if (active && generation === auditGeneration && exception.name !== 'AbortError') auditError.value = exception.message
  } finally {
    if (active && generation === auditGeneration) auditLoading.value = false
  }
}

async function submit() {
  if (saving.value || resolved.value || !canSubmit.value) return
  saving.value = true
  error.value = ''
  try {
    await resolveEmailDelivery(props.delivery.delivery_id, {
      message_id: props.delivery.message_id,
      evidence_note: evidence.value.trim(),
      outcomes: pendingRecipients.value.map((recipient) => ({ recipient, outcome: outcomes[recipient] })),
    })
    if (!mounted) return
    resolved.value = true
    if (active) {
      emit('dirty', false)
      emit('resolved')
      await loadAudit()
    }
  } catch (exception) {
    if (mounted) error.value = exception.message
  } finally {
    saving.value = false
  }
}

function close() {
  if (saving.value) return
  if (!resolved.value && dirty.value && !window.confirm('核对内容尚未保存，确定关闭吗？')) return
  emit('close')
}
function outcomeText(status) {
  return { accepted: '服务器已接收', not_accepted: '服务器未接收', unknown: '结果待核对', refused: '服务器拒收' }[status] || status
}
onMounted(loadAudit)
onDeactivated(() => { active = false; auditController?.abort() })
onActivated(() => {
  if (!active) {
    active = true
    if (resolved.value) { emit('dirty', false); emit('resolved') }
    loadAudit()
  }
})
onUnmounted(() => { mounted = false; active = false; auditController?.abort(); emit('dirty', false) })
</script>

<template>
  <section class="panel resolution-panel" aria-labelledby="resolution-title">
    <header><h2 id="resolution-title">邮件结果核对 · {{ delivery.schedule_name }}</h2><button type="button" :disabled="saving" @click="close">关闭</button></header>
    <p class="message-id">Message-ID：{{ delivery.message_id || '无历史 Message-ID，无法核对提交' }}</p>
    <form v-if="delivery.status === 'unknown' && !resolved" @submit.prevent="submit">
      <p>请先按 Message-ID 核对邮件服务器记录，再选择每位收件人的结果。保存核对不会发送邮件；确认未接收后仍需单独手动重试。</p>
      <fieldset :disabled="saving || !delivery.message_id">
        <legend>逐一确认服务器接收结果</legend>
        <label v-for="address in pendingRecipients" :key="address" class="recipient-choice">
          <span>{{ address }}</span>
          <select v-model="outcomes[address]" required :aria-label="`${address} 接收结果`">
            <option :value="undefined" disabled>请选择核对结果</option><option value="accepted">服务器已接收</option><option value="not_accepted">服务器未接收</option>
          </select>
        </label>
        <label class="evidence-label"><span>核对依据（例如服务器日志时间与记录编号）</span><textarea v-model="evidence" name="verification_evidence" required maxlength="1000" rows="3"></textarea></label>
      </fieldset>
      <p v-if="error" role="alert" class="resolution-error">{{ error }}。填写内容已保留；状态冲突时请关闭并刷新发送记录。</p>
      <button type="submit" :disabled="saving || !canSubmit">{{ saving ? '正在保存核对…' : '保存核对结果' }}</button>
    </form>
    <p v-if="resolved" role="status">核对已保存。确认未接收的收件人仍需在发送记录中手动重试。</p>
    <h3>核对记录</h3>
    <p v-if="auditLoading" role="status">正在读取核对记录…</p>
    <p v-else-if="auditError" role="alert" class="resolution-error">{{ auditError }} <button type="button" @click="loadAudit">重新读取</button></p>
    <p v-else-if="!audits.length">暂无人工核对记录。</p>
    <article v-for="item in audits" :key="item.resolution_id" class="resolution-audit">
      <strong>{{ item.actor_name }} · {{ formatChineseDateTime(item.created_at) }}</strong>
      <p>{{ item.evidence_note }}</p>
      <ul><li v-for="(result, address) in item.after_results" :key="address">{{ address }}：{{ outcomeText(item.before_results?.[address]?.status || 'unknown') }} → {{ outcomeText(result.status) }}</li></ul>
    </article>
  </section>
</template>

<style scoped>
.resolution-panel{padding:20px;margin-top:16px;color:var(--ink)}header{display:flex;align-items:center;justify-content:space-between;gap:12px}h2{font-size:16px;margin:0}h3{font-size:14px;margin-top:24px}.message-id,p,li{font-size:12px;line-height:1.7;overflow-wrap:anywhere}fieldset{border:1px solid var(--line);padding:14px;margin:14px 0}legend{font-size:12px}.recipient-choice{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,1fr);align-items:center;gap:12px;margin-bottom:12px}.recipient-choice span{overflow-wrap:anywhere;font-size:12px}select,textarea{width:100%;box-sizing:border-box;min-height:40px;border:1px solid var(--theme-border-color);background:rgb(var(--container-bg-color));color:var(--ink);padding:8px}textarea{resize:vertical}.evidence-label span{display:block;font-size:12px;margin:12px 0 6px}.resolution-error{color:rgb(var(--error-color))}.resolution-audit{border-top:1px solid var(--line);padding:12px 0}.resolution-audit strong{font-size:12px}.resolution-audit ul{padding-left:20px}button{min-height:36px}select:focus-visible,textarea:focus-visible,button:focus-visible{outline:3px solid var(--cyan);outline-offset:2px}@media(max-width:560px){.resolution-panel{padding:14px}.recipient-choice{grid-template-columns:1fr;gap:5px}header{align-items:flex-start}}
</style>
