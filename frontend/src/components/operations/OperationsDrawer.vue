<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { NAlert, NButton, NDrawer, NDrawerContent, NInput, NSpin, NTag } from 'naive-ui'
import type { Incident, LogRecord, RelatedOperation } from '../../api/generated/types.gen'
import { operations } from '../../api/operations'
import { incidentExplanation, incidentLevels, incidentLogQuery, incidentStatus, operationStages, roles, stamp, tone, workStatus } from './presentation'
import { deviceSelection } from '../../state/devices'

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
const explanation = computed(() => record.value && (incident.value || ['WARNING', 'ERROR', 'CRITICAL'].includes(record.value.level))
  ? incidentExplanation(record.value.error_code, record.value.message) : null)
const lineName = computed(() => record.value?.line_id
  ? deviceSelection.items.find(line => line.id === record.value?.line_id)?.name || '产线名称未登记'
  : '公共系统')
const dataLabels = {
  period_start: '影响开始日期', period_end: '影响结束日期', data_as_of: '数据截至时间',
  missing_count: '缺失项数', estimated_count: '插值及估算项数',
  summary_count: '汇总作业数', daily_count: '按日合计', grouped_count: '按设备或工程合计',
} as const
const dataDetails = computed(() => (Object.keys(dataLabels) as Array<keyof typeof dataLabels>).flatMap(key => {
  const value = incident.value?.context[key] ?? log?.data_quality?.[key]
  return value == null ? [] : [{ label: dataLabels[key], value: key === 'data_as_of' ? value.replace('T', ' ') : value }]
}))
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
            <NTag size="small" :type="tone(record.level)">{{ incidentLevels[record.level] }}</NTag>
            <NTag v-if="incident" size="small">{{ incidentStatus[incident.status] }}</NTag>
            <h3>{{ record.message }}</h3>
          </div>
          <div v-if="explanation" class="explanation">
            <h4>发生了什么</h4><p>{{ explanation.meaning }}</p>
            <h4>有什么影响</h4><p>{{ explanation.impact }}</p>
            <h4>建议怎么处理</h4><p>{{ explanation.action }}</p>
          </div>
          <dl class="details">
            <dt>影响产线</dt><dd>{{ lineName }}</dd>
            <dt v-if="context?.stage">处理环节</dt><dd v-if="context?.stage">{{ operationStages[context.stage] || '其他处理环节（见技术详情）' }}</dd>
            <template v-if="incident">
              <dt>首次发生</dt><dd>{{ stamp(incident.first_seen_at) }}</dd>
              <dt>最近发生</dt><dd>{{ stamp(incident.last_seen_at) }}</dd>
              <dt>发生次数</dt><dd>{{ incident.occurrences }}</dd>
            </template>
            <template v-if="log">
              <dt>时间</dt><dd>{{ stamp(log.timestamp) }}</dd>
              <dt>耗时</dt><dd>{{ log.duration_ms == null ? '—' : log.duration_ms + ' ms' }}</dd>
              <dt v-if="log.run_id">计划执行 ID</dt><dd v-if="log.run_id">{{ log.run_id }}</dd>
              <dt v-if="log.attempt != null">发送尝试</dt><dd v-if="log.attempt != null">{{ log.attempt }}</dd>
              <dt v-if="log.error_type">错误类型</dt><dd v-if="log.error_type">{{ log.error_type }}</dd>
            </template>
            <template v-for="detail in dataDetails" :key="detail.label">
              <dt>{{ detail.label }}</dt><dd>{{ detail.value }}</dd>
            </template>
          </dl>
          <p v-if="record.error_code === 'ENERGY_TASK_DATA_QUALITY' || record.error_code === 'ENERGY_DAY_DATA_QUALITY'" class="muted">缺失、插值及估算按读数项统计，并非任务数。</p>
          <section v-if="linkedRecord">
            <h4>关联业务记录</h4>
            <p v-if="relatedLoading" role="status">正在读取关联记录</p>
            <NAlert v-else-if="relatedError" type="warning">{{ relatedError }}</NAlert>
            <dl v-else-if="related" class="details">
              <dt>处理状态</dt><dd>{{ workStatus[related.status] || '其他状态（见技术详情）' }}</dd>
              <dt v-if="related.stage">处理内容</dt><dd v-if="related.stage">{{ operationStages[related.stage] || '其他处理环节（见技术详情）' }}</dd>
            </dl>
            <p v-if="related?.status === 'completed' && incident?.status !== 'recovered' && record.error_code?.endsWith('_DATA_QUALITY')" class="muted">关联任务已完成，只表示处理流程结束，不代表数据问题已恢复；仍需等待同一范围的数据重新校验通过。</p>
          </section>
          <section v-if="incident">
            <h4>处理记录</h4>
            <dl class="details" v-if="incident.acknowledged_at || incident.recovered_at">
              <template v-if="incident.acknowledged_at">
                <dt>确认时间</dt><dd>{{ stamp(incident.acknowledged_at) }}</dd>
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
              <p class="muted">确认表示已知悉，不代表异常已修复。</p>
            </template>
          </section>
          <details class="technical">
            <summary>技术详情（供维护人员排查）</summary>
            <dl class="details">
              <dt>来源</dt><dd>{{ roles[record.role] }} {{ record.instance_id }} / {{ record.component }}</dd>
              <dt>错误代码</dt><dd>{{ record.error_code || '—' }}</dd>
              <dt v-if="record.line_id">产线编号</dt><dd v-if="record.line_id">{{ record.line_id }}</dd>
              <dt v-if="record.source_id">数据源标识</dt><dd v-if="record.source_id">{{ record.source_id }}</dd>
              <template v-for="field in [
                { label: '请求 ID', value: context?.request_id }, { label: '分析 ID', value: context?.analysis_id },
                { label: '后台任务 ID', value: context?.task_id }, { label: '报表 ID', value: context?.report_id },
                { label: '关联资源 ID', value: record.resource_id }, { label: '阶段代码', value: context?.stage },
                { label: '报表版本', value: incident?.context.data_version || log?.data_quality?.data_version },
                { label: '关联记录 ID', value: related?.id }, { label: '关联状态代码', value: related?.status },
                { label: '关联阶段代码', value: related?.stage }, { label: '确认管理员 ID', value: incident?.acknowledged_by },
              ]" :key="field.label">
                <template v-if="field.value"><dt>{{ field.label }}</dt><dd>{{ field.value }}</dd></template>
              </template>
            </dl>
          </details>
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
.explanation { margin-top:16px; }
.explanation h4 { margin:16px 0 4px; font-size:var(--font-body); }
.explanation p, .muted { margin:4px 0 12px; line-height:1.7; }
.muted { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.technical { border-top:1px solid var(--theme-divider-color); padding-top:16px; }
summary { cursor:pointer; }
.details { display:grid; grid-template-columns:minmax(96px, auto) minmax(0,1fr); gap:8px 16px; margin:12px 0 20px; }
.details dt { color:var(--text-color-3); }
.details dd { margin:0; overflow-wrap:anywhere; }
section { border-top:1px solid var(--theme-divider-color); margin-top:12px; padding:12px 0; }
section h4 { margin:0 0 8px; font-size:var(--font-section-title); }
details pre { white-space:pre-wrap; overflow-wrap:anywhere; padding:12px; background:var(--theme-code-color); font-size:var(--font-auxiliary); }
</style>
