<script setup>
import { computed, onUnmounted, ref, watch } from 'vue'
import { applyWorkshopSourceCorrections, previewWorkshopSourceCorrections } from '../api/reports.js'
import { useDialogFocus } from '../composables/useDialogFocus.js'
import { formatChineseDateTime } from '../utils/dateDisplay.js'

const props = defineProps({ reportDate: { type: String, required: true }, active: { type: Boolean, default: true } })
const emit = defineEmits(['updated'])
const visible = ref(false)
const dialog = ref(null)
const reportDate = ref(props.reportDate)
const preview = ref(null)
const reason = ref('')
const reviewed = ref(false)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const feedback = ref('')
const revokedCount = computed(() => preview.value?.items.filter((item) => item.previous_confirmation).length || 0)
let controller = null
let sequence = 0
let live = true
let deferredUpdate = false
useDialogFocus(visible, dialog, close)

const fieldLabels = { project_name: '工程/船号', segment_name: '分段名', program_no: '程序号',
  cut_length: '切割长度', groove_length: '坡口长度', work_time: '切割时长（秒）',
  production_time: '生产时间', production_date: '生产日期', remark: '备注' }

function cancelRead() {
  sequence++
  controller?.abort()
  controller = null
  loading.value = false
}

function open() {
  if (!props.active || saving.value) return
  reportDate.value = props.reportDate
  preview.value = null
  reason.value = ''
  reviewed.value = false
  error.value = ''
  feedback.value = ''
  visible.value = true
}

function close() {
  if (saving.value) return
  cancelRead()
  visible.value = false
}

async function loadPreview() {
  if (!live || !visible.value || !props.active || saving.value) return
  cancelRead()
  const current = ++sequence
  controller = new AbortController()
  const signal = controller.signal
  const selectedDate = reportDate.value
  loading.value = true
  preview.value = null
  reviewed.value = false
  error.value = ''
  try {
    const data = await previewWorkshopSourceCorrections(selectedDate, { signal })
    if (live && visible.value && props.active && current === sequence && !signal.aborted) preview.value = data
  } catch (exception) {
    if (live && current === sequence && exception.name !== 'AbortError') error.value = exception.message
  } finally {
    if (live && current === sequence) { loading.value = false; controller = null }
  }
}

async function submit() {
  if (!live || !props.active || !visible.value || saving.value || loading.value) return
  if (!preview.value?.items.length || !reviewed.value || reason.value.trim().length < 2) {
    error.value = '请先预览并核对差异，填写至少 2 个字的更正原因'
    return
  }
  saving.value = true
  error.value = ''
  try {
    const result = await applyWorkshopSourceCorrections({ reportDate: reportDate.value,
      expectedVersion: preview.value.version, reason: reason.value.trim() })
    if (!live) return
    preview.value = null
    reviewed.value = false
    if (!props.active) { deferredUpdate = true; return }
    feedback.value = `已更正 ${result.corrected_count} 项任务，撤销 ${result.revoked_count} 项原确认；受影响任务需要重新确认。`
    emit('updated')
  } catch (exception) {
    if (!live) return
    preview.value = null
    reviewed.value = false
    error.value = `${exception.message}。请重新预览，核对当前记录和更正历史后再操作。`
  } finally {
    if (live) saving.value = false
  }
}

watch(reportDate, () => { cancelRead(); preview.value = null; reviewed.value = false; feedback.value = '' })
watch(() => props.active, (active) => {
  if (!active) { cancelRead(); visible.value = false; preview.value = null; reviewed.value = false }
  else if (deferredUpdate) { deferredUpdate = false; emit('updated') }
})
onUnmounted(() => { live = false; cancelRead() })
</script>

<template>
  <button type="button" :disabled="saving || !active" @click="open">更正日报源记录</button>
  <Teleport to="body">
    <div v-if="visible" class="source-correction-backdrop" @click.self="close">
      <section ref="dialog" class="source-correction-dialog" role="dialog" aria-modal="true" aria-labelledby="source-correction-title" tabindex="-1">
        <header><h2 id="source-correction-title">更正日报源记录</h2><button type="button" :disabled="saving" aria-label="关闭更正窗口" @click="close">×</button></header>
        <p>核对日报与生产源记录的差异后再更正。更正会撤销受影响任务的原确认，并保留旧值、新值和操作记录；其他任务的确认保持不变。</p>
        <form @submit.prevent="loadPreview"><label>日报日期<AppDatePicker v-model="reportDate" :disabled="saving" required /></label><button type="submit" :disabled="loading || saving">{{ loading ? '正在读取差异…' : '重新读取并预览差异' }}</button></form>
        <p v-if="feedback" class="source-correction-success" role="status">{{ feedback }}</p>
        <p v-if="error" class="source-correction-error" role="alert">{{ error }}</p>
        <template v-if="preview">
          <p v-if="!preview.items.length">当前没有需要更正的源记录。新增任务通过日报补采处理。</p>
          <p v-else>本次核对 {{ preview.items.length }} / {{ preview.total_changes }} 项变化，提交后将撤销 {{ revokedCount }} 项原确认。每次最多更正 100 项，其余差异需重新预览后逐批处理。</p>
          <div v-if="preview.items.length" class="source-correction-table">
            <table v-content-table="active && visible"><thead><tr><th>任务</th><th>字段</th><th>日报旧值</th><th>源记录新值</th><th>原确认人</th></tr></thead><tbody>
              <template v-for="item in preview.items" :key="item.task_id"><tr v-for="field in item.fields" :key="`${item.task_id}:${field}`"><td>{{ item.task_id }}</td><td>{{ fieldLabels[field] || field }}</td><td>{{ item.old[field] ?? '--' }}</td><td>{{ item.new[field] ?? '--' }}</td><td>{{ item.previous_confirmation?.worker_name || '未确认' }}</td></tr></template>
            </tbody></table>
          </div>
          <p v-if="preview.items.length" class="source-correction-table-note">左右滑动表格，核对旧值和新值。</p>
          <template v-if="preview.items.length">
            <label class="source-correction-reason">更正原因<textarea v-model="reason" maxlength="200" :disabled="saving" rows="2" /></label>
            <label class="source-correction-review"><input v-model="reviewed" type="checkbox" :disabled="saving" />我已核对上述差异，了解原确认将撤销并需要重新确认</label>
            <button class="primary" type="button" :disabled="saving || !reviewed || reason.trim().length < 2" @click="submit">{{ saving ? '正在提交更正…' : `更正这 ${preview.items.length} 项任务` }}</button>
          </template>
          <details v-if="preview.audits?.length"><summary>最近 {{ preview.audits.length }} 条更正记录</summary><article v-for="audit in preview.audits" :key="audit.audit_id"><strong>{{ audit.task_id }} · {{ audit.operated_by_name }} · {{ formatChineseDateTime(audit.operated_at) }}</strong><p>{{ audit.reason }}<span v-if="audit.payload.previous_confirmation"> · 已撤销 {{ audit.payload.previous_confirmation.worker_name }} 的原确认</span></p><ul><li v-for="field in audit.payload.fields" :key="field">{{ fieldLabels[field] || field }}：{{ audit.payload.old[field] ?? '--' }} → {{ audit.payload.new[field] ?? '--' }}</li></ul></article></details>
        </template>
        <footer><button type="button" :disabled="saving" @click="close">关闭</button></footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.source-correction-backdrop { position:fixed; inset:0; z-index:1000; display:flex; align-items:center; justify-content:center; padding:20px; background:rgba(15,35,54,.45); }
.source-correction-dialog { width:min(940px,100%); max-height:90vh; overflow:auto; padding:24px; border-radius:12px; background:#fff; color:#233e51; box-shadow:0 20px 60px rgba(15,35,54,.2); }
header,form { display:flex; align-items:center; justify-content:space-between; gap:16px; } h2 { margin:0; font-size:20px; } p { line-height:1.65; } form { justify-content:flex-start; flex-wrap:wrap; } form label,.source-correction-reason { display:flex; flex-direction:column; gap:8px; }
.source-correction-table { overflow:auto; max-height:40vh; margin:16px 0; } table { width:100%; border-collapse:collapse; font-size:13px; } th,td { padding:10px; text-align:left; border-bottom:1px solid #dce5eb; overflow-wrap:anywhere; } th { background:#eff4f7; }
.source-correction-reason { margin:16px 0; } textarea { width:100%; box-sizing:border-box; padding:10px; font:inherit; border:1px solid #c8d6e1; border-radius:8px; resize:vertical; } .source-correction-review { display:flex; align-items:flex-start; justify-content:flex-start; gap:8px; margin-bottom:16px; } .source-correction-review input { width:18px; height:18px; min-height:18px; flex:0 0 18px; margin:3px 0 0; padding:0; } .source-correction-error { color:#a52638; } .source-correction-success { color:#087443; } .source-correction-table-note { display:none; }
details { margin-top:20px; } summary { cursor:pointer; } article { padding:14px 0; border-bottom:1px solid #dce5eb; } article p { margin:6px 0; } footer { display:flex; justify-content:flex-end; margin-top:20px; }
@media(max-width:600px) { .source-correction-backdrop { padding:10px; } .source-correction-dialog { padding:16px; max-height:92dvh; } header { align-items:flex-start; } h2 { font-size:18px; } form>label { width:100%; } .source-correction-table-note { display:block; font-size:12px; color:#526d80; } }
</style>
