<script setup lang="ts">
import type { AnalysisBrief, AnalysisFact, BriefFinding, BusinessReference } from '../api/generated/types.gen'
import { computed, shallowRef, watch } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCollapse, NCollapseItem, NModal } from 'naive-ui'
import AppReportTable from './AppReportTable.vue'
import { getDeviceId } from '../api/deviceContext.ts'
import { analysisTaskLocation } from '../utils/analysisReferences.ts'

const props = defineProps<{ brief: AnalysisBrief; facts: AnalysisFact[]; generatedAt?: string | null }>()
const router = useRouter()
const selected = shallowRef<BriefFinding | null>(null)
const referencePage = shallowRef(1)
const visibleReferences = computed(() => selected.value?.references.slice((referencePage.value - 1) * 10, referencePage.value * 10) || [])
watch(selected, () => { referencePage.value = 1 })
const metrics = computed(() => props.brief.key_metric_ids.flatMap(id => props.facts.filter(fact => fact.id === id)))
const previews = computed(() => props.brief.findings.map(finding => ({ ...finding, preview: finding.references.slice(0, 3) })))
const taskCount = computed(() => selected.value?.references.filter(reference => reference.kind === 'task').length || 0)
const columns = [
  { key: 'label', title: '关联对象', align: 'left' },
  { key: 'production_date', title: '生产日期', align: 'left' },
  { key: 'project_code', title: '工程号', align: 'left' },
  { key: 'program_no', title: '程序号', align: 'left' },
  { key: 'confirmation', title: '工人确认', align: 'left' },
  { key: 'energy_status', title: '能耗记录状态', align: 'left' },
  { key: 'c6', title: '操作', align: 'center', ellipsis: false },
] as const
function canOpen(reference: BusinessReference) { return Boolean(analysisTaskLocation(reference, getDeviceId())) }
function openTask(reference: BusinessReference) {
  const location = analysisTaskLocation(reference, getDeviceId())
  if (!location) return
  selected.value = null
  void router.push(location)
}
watch(() => props.brief, () => { selected.value = null })
</script>

<template>
  <div class="cutting-brief">
    <p class="brief-period">{{ brief.scope.line_name }} · {{ brief.scope.period_start }} 至 {{ brief.scope.period_end }}</p>
    <section class="brief-conclusion" aria-label="分析结论">
      <h3>{{ brief.headline }}</h3><p>{{ brief.summary }}</p>
    </section>
    <dl v-if="metrics.length" class="brief-metrics" aria-label="关键指标">
      <div v-for="fact in metrics" :key="fact.id || fact.label"><dt>{{ fact.label }}</dt><dd>{{ fact.value }}</dd></div>
    </dl>
    <section v-if="previews.length" class="brief-findings" aria-label="关键发现">
      <h3>关键发现</h3>
      <article v-for="finding in previews" :key="finding.candidate_id">
        <h4>{{ finding.title }}</h4><p>{{ finding.detail }}</p>
        <div v-if="finding.reference_count" class="brief-links">
          <template v-for="reference in finding.preview" :key="`${reference.kind}:${reference.id}`">
            <NButton v-if="canOpen(reference)" text type="primary" @click="openTask(reference)">任务 {{ reference.label }}</NButton>
            <span v-else>{{ reference.label }}</span>
          </template>
          <NButton text type="primary" @click="selected = finding">{{ finding.references.every(r => r.kind === 'task') ? '查看全部关联任务' : '查看全部关联记录' }}（{{ finding.reference_count }} 项）</NButton>
        </div>
      </article>
    </section>
    <section v-if="brief.actions.length" class="brief-followup" aria-label="建议跟进">
      <h3>建议跟进</h3>
      <article v-for="(action, index) in brief.actions" :key="`${action.candidate_id}:${index}`"><h4>{{ action.title }}</h4><p>{{ action.detail }}</p></article>
    </section>
    <NCollapse class="brief-evidence"><NCollapseItem title="分析依据" name="evidence">
      <p>统计周期：{{ brief.scope.period_start }} 至 {{ brief.scope.period_end }}</p>
      <p v-if="brief.scope.comparison_start">比较周期：{{ brief.scope.comparison_start }} 至 {{ brief.scope.comparison_end }}</p>
      <ul><li v-for="item in brief.data_quality" :key="item">{{ item }}</li></ul>
      <dl class="brief-facts"><div v-for="fact in facts" :key="fact.id || fact.label"><dt>{{ fact.label }} · {{ fact.value }}</dt><dd>{{ fact.source }}</dd></div></dl>
    </NCollapseItem></NCollapse>
    <NModal :show="Boolean(selected)" preset="card" title="关联业务记录" class="brief-record-modal" style="width:min(1100px, calc(100vw - 32px))" @update:show="shown => { if (!shown) selected = null }">
      <template v-if="selected">
        <p>分析时记录，共 {{ selected.reference_count }} 项关联，其中任务 {{ taskCount }} 项。生成时间：{{ generatedAt || '--' }}。点击“查看当前任务”按原产线和周期重新查询。</p>
        <AppReportTable :columns="columns" :items="visibleReferences" :total="selected.references.length" :page="referencePage" :page-sizes="[]" @page="referencePage = $event" :row-key="row => `${row.kind}:${row.id}`" :page-size="10">
          <template #c6="{ row }"><NButton v-if="canOpen(row)" text type="primary" @click="openTask(row)">查看当前任务</NButton><span v-else>—</span></template>
        </AppReportTable>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.cutting-brief { padding:16px; font-size:13px; color:var(--text-color-1); overflow-wrap:anywhere; }
.brief-period { margin:0 0 12px; color:var(--text-color-2); font-size:var(--font-auxiliary); }
.brief-conclusion h3 { margin:0 0 8px; font-size:20px; line-height:1.5; }
p { margin:0; max-width:75ch; line-height:1.8; white-space:pre-wrap; }
.brief-findings,.brief-followup,.brief-evidence { margin-top:24px; }
.brief-findings>h3,.brief-followup>h3 { margin:0 0 12px; font-size:var(--font-section-title); }
article+article { margin-top:16px; }
h4 { margin:0 0 4px; font-size:13px; font-weight:600; }
.brief-links { display:flex; flex-wrap:wrap; align-items:center; gap:8px 16px; margin-top:8px; font-size:var(--font-auxiliary); color:var(--text-color-2); }
.brief-metrics { display:flex; flex-wrap:wrap; gap:16px 24px; margin:16px 0 0; }
.brief-metrics dt { color:var(--text-color-2); font-size:var(--font-auxiliary); }
.brief-metrics dd { margin:4px 0 0; font-size:20px; font-weight:600; font-variant-numeric:tabular-nums; }
.brief-facts div+div { margin-top:12px; }.brief-facts dt { font-weight:600; }.brief-facts dd { margin:4px 0 0; color:var(--text-color-2); }
.brief-evidence { padding-top:12px; border-top:1px solid var(--theme-border-color); font-size:var(--font-auxiliary); }
.brief-evidence li { margin-bottom:8px; line-height:1.7; }
@media(max-width:600px) { .brief-conclusion h3 { font-size:var(--font-section-title); }.brief-metrics { flex-direction:column; } }
</style>
