<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NDrawer, NDrawerContent, NInput, NSpin, NTag } from 'naive-ui'
import type { Incident, LogRecord, RelatedOperation } from '../../api/generated/types.gen'
import { operations } from '../../api/operations'
import { incidentLogQuery, incidentStatus, roles, stamp, tone, workStatus } from './presentation'

const { incidentId, log } = defineProps<{ incidentId: string | null; log: LogRecord | null }>()
const emit = defineEmits<{ close: []; changed: [Incident] }>()
const incident = shallowRef<Incident | null>(null)
const related = shallowRef<RelatedOperation | null>(null)
const note = shallowRef('')
const error = shallowRef('')
const relatedError = shallowRef('')
const loading = shallowRef(false)
const saving = shallowRef(false)
const relatedLoading = shallowRef(false)
const copied = shallowRef(false)
const visible = computed(() => !!(incidentId || log))
const record = computed(() => incident.value || log)
const context = computed(() => incident.value?.context ?? log)
const linkedRecord = computed(() => {
  const kind = incident.value?.context.related_kind || log?.related_kind
  const id = kind === 'reports' ? context.value?.report_id || record.value?.resource_id
    : kind === 'background' ? context.value?.task_id || record.value?.resource_id
      : record.value?.resource_id
  if (kind && id && isRelatedKind(kind)) return { kind, id }
  if (context.value?.report_id) return { kind: 'reports' as const, id: context.value.report_id }
  if (context.value?.task_id) return { kind: 'background' as const, id: context.value.task_id }
  return null
})

function isRelatedKind(kind: string): kind is RelatedOperation['kind'] {
  return ['reports', 'background', 'automation', 'email', 'database-monitor', 'dispatch'].includes(kind)
}

watch(() => [incidentId, log?.id], async (_, __, onCleanup) => {
  incident.value = null
  note.value = ''
  error.value = ''
  loading.value = !!incidentId
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  if (!incidentId) return
  try {
    const item = await operations.incident(incidentId, controller.signal)
    if (!controller.signal.aborted) incident.value = item
  }
  catch (caught) { if (!controller.signal.aborted) error.value = String(caught) }
  finally { if (!controller.signal.aborted) loading.value = false }
}, { immediate: true })

watch(linkedRecord, async (link, _, onCleanup) => {
  related.value = null
  relatedError.value = ''
  relatedLoading.value = !!link
  if (!link) return
  const controller = new AbortController()
  onCleanup(() => controller.abort())
  try {
    const item = await operations.related(link.kind, link.id, controller.signal)
    if (!controller.signal.aborted) related.value = item
  }
  catch (caught) { if (!controller.signal.aborted) relatedError.value = String(caught) }
  finally { if (!controller.signal.aborted) relatedLoading.value = false }
}, { immediate: true })

const logLink = computed(() => incident.value ? incidentLogQuery(incident.value) : {})

async function acknowledge() {
  if (!incidentId || saving.value) return
  const id = incidentId
  saving.value = true
  error.value = ''
  try {
    const updated = await operations.acknowledge(id, { note: note.value.trim() || null })
    if (incidentId === id) incident.value = updated
    emit('changed', updated)
  } catch (caught) {
    if (incidentId === id) error.value = String(caught)
  } finally { saving.value = false }
}

async function copyLog() {
  if (!log) return
  try {
    await navigator.clipboard.writeText(JSON.stringify(log, null, 2))
    copied.value = true
  } catch { error.value = '复制失败，可展开结构化字段后手动复制。' }
}
watch(() => log?.id, () => { copied.value = false })
</script>

<template>
  <NDrawer :show="visible" width="min(560px, 100vw)" @update:show="value => !value && emit('close')">
    <NDrawerContent :title="incidentId ? '异常事件详情' : '运行日志详情'" closable>
      <NAlert v-if="error" type="error" class="mb-12px">{{ error }}</NAlert>
      <NSpin :show="loading">
        <template v-if="record">
          <div class="headline">
            <NTag size="small" :type="tone(record.level)">{{ record.level }}</NTag>
            <NTag v-if="incident" size="small">{{ incidentStatus[incident.status] }}</NTag>
            <h3>{{ record.message }}</h3>
          </div>
          <dl class="details">
            <dt>来源</dt><dd>{{ roles[record.role] }} {{ record.instance_id }} / {{ record.component }}</dd>
            <dt>错误代码</dt><dd>{{ record.error_code || '—' }}</dd>
            <dt>产线 / 数据源</dt><dd>{{ record.line_id || '—' }} / {{ record.source_id || '—' }}</dd>
            <template v-if="incident">
              <dt>首次发生</dt><dd>{{ stamp(incident.first_seen_at) }}</dd>
              <dt>最近发生</dt><dd>{{ stamp(incident.last_seen_at) }}</dd>
              <dt>发生次数</dt><dd>{{ incident.occurrences }}</dd>
            </template>
            <template v-if="log">
              <dt>时间</dt><dd>{{ stamp(log.timestamp) }}</dd>
              <dt>耗时</dt><dd>{{ log.duration_ms == null ? '—' : log.duration_ms + ' ms' }}</dd>
            </template>
            <dt>请求 ID</dt><dd>{{ context?.request_id || '—' }}</dd>
            <dt>分析 ID</dt><dd>{{ context?.analysis_id || '—' }}</dd>
            <dt>后台任务 ID</dt><dd>{{ context?.task_id || '—' }}</dd>
            <dt>报表 ID</dt><dd>{{ context?.report_id || '—' }}</dd>
            <dt v-if="record.resource_id">关联资源 ID</dt><dd v-if="record.resource_id">{{ record.resource_id }}</dd>
            <dt v-if="context?.stage">阶段</dt><dd v-if="context?.stage">{{ context.stage }}</dd>
          </dl>
          <section v-if="linkedRecord">
            <h4>关联业务记录</h4>
            <p v-if="relatedLoading" role="status">正在读取关联记录</p>
            <NAlert v-else-if="relatedError" type="warning">{{ relatedError }}</NAlert>
            <dl v-else-if="related" class="details">
              <dt>记录 ID</dt><dd>{{ related.id }}</dd>
              <dt>当前状态</dt><dd>{{ workStatus[related.status] || related.status }}</dd>
              <dt v-if="related.stage">业务类型或阶段</dt><dd v-if="related.stage">{{ related.stage }}</dd>
            </dl>
          </section>
          <section v-if="incident">
            <h4>处理记录</h4>
            <dl class="details" v-if="incident.acknowledged_at || incident.recovered_at">
              <template v-if="incident.acknowledged_at">
                <dt>确认时间</dt><dd>{{ stamp(incident.acknowledged_at) }}</dd>
                <dt>管理员 ID</dt><dd>{{ incident.acknowledged_by }}</dd>
                <dt>处理备注</dt><dd>{{ incident.note || '—' }}</dd>
              </template>
              <template v-if="incident.recovered_at">
                <dt>恢复时间</dt><dd>{{ stamp(incident.recovered_at) }}</dd>
                <dt>恢复依据</dt><dd>{{ incident.recovery_evidence }}</dd>
              </template>
            </dl>
            <p v-else>暂无处理记录。</p>
            <template v-if="incident.status === 'open'">
              <NInput v-model:value="note" placeholder="填写确认备注（选填）" aria-label="确认备注" maxlength="240" />
              <NButton class="mt-8px" type="primary" :loading="saving" @click="acknowledge">确认异常</NButton>
            </template>
          </section>
          <details v-if="log">
            <summary>结构化字段和安全堆栈</summary>
            <pre>{{ JSON.stringify(log, null, 2) }}</pre>
          </details>
        </template>
      </NSpin>
      <template #footer>
        <RouterLink v-if="incident" :to="{ name: 'operations-logs', query: logLink }" @click="emit('close')">检索相关日志</RouterLink>
        <NButton v-if="log" @click="copyLog">{{ copied ? '已复制' : '复制日志' }}</NButton>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.headline { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.headline h3 { margin:0; font-size:var(--font-section-title); overflow-wrap:anywhere; }
.details { display:grid; grid-template-columns:minmax(96px, auto) minmax(0,1fr); gap:8px 16px; margin:12px 0 20px; }
.details dt { color:var(--text-color-3); }
.details dd { margin:0; overflow-wrap:anywhere; }
section { border-top:1px solid var(--theme-divider-color); margin-top:12px; padding:12px 0; }
section h4 { margin:0 0 8px; font-size:var(--font-section-title); }
details pre { white-space:pre-wrap; overflow-wrap:anywhere; padding:12px; background:var(--theme-code-color); font-size:var(--font-auxiliary); }
</style>
