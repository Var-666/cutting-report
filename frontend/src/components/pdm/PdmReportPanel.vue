<script setup lang="ts">
import { onBeforeUnmount, ref, shallowRef } from 'vue'
import { NAlert, NButton, NModal } from 'naive-ui'
import PdmChartTable from './PdmChartTable.vue'
import PdmFilterBar from './PdmFilterBar.vue'
import ReportPanelState from '../ReportPanelState.vue'
import { getPdmReport } from '../../api/pdmStatistics'
import { pollReportUpdates, type ReportReadMode } from '../../api/reportRequests'
import ReportDataStatus from '../ReportDataStatus.vue'
import { usePdmFilterOptions } from '../../composables/usePdmFilterOptions'
import { clonePdmFilters } from '../../utils/pdmFilters'
import { pdmReportRegion } from '../../utils/pdmReport'
import type { PdmReportRegion } from '../../utils/pdmReport'
import type { PdmFilters } from '../../utils/pdmFilters'

const props = defineProps<{ region: PdmReportRegion }>()
const emit = defineEmits<{ update: [region: PdmReportRegion]; remember: [filters: PdmFilters] }>()
const visible = ref(false)
const draft = ref(clonePdmFilters(props.region.filters))
const querying = shallowRef(false)
const queryError = shallowRef('')
let reportController: AbortController | undefined
const { options, status, error, retry } = usePdmFilterOptions(draft, visible, props.region.section.kind)

function openFilters() {
  draft.value = clonePdmFilters(props.region.filters)
  queryError.value = ''
  visible.value = true
}
async function query(filters: PdmFilters, mode: ReportReadMode = 'read') {
  const submitted = clonePdmFilters(filters)
  submitted.documentKinds = [props.region.section.kind]
  submitted.groupBy = props.region.section.groupBy
  reportController?.abort()
  const controller = new AbortController()
  reportController = controller
  querying.value = true
  queryError.value = ''
  try {
    const result = await getPdmReport(submitted, controller.signal, { mode })
    if (controller !== reportController) return
    const region = pdmReportRegion(props.region.section, submitted, result, options.value)
    querying.value = false
    visible.value = false
    emit('remember', submitted)
    emit('update', region)
    void pollReportUpdates(result, () => getPdmReport(submitted, controller.signal, { mode: 'poll' }), value => {
      if (controller === reportController) emit('update', pdmReportRegion(props.region.section, submitted, value, options.value))
    }, controller.signal).catch(caught => {
      if (controller === reportController && !controller.signal.aborted) queryError.value = caught instanceof Error ? caught.message : String(caught)
    })
  } catch (caught) {
    if (controller !== reportController || controller.signal.aborted) return
    queryError.value = caught instanceof Error ? caught.message : String(caught)
  } finally {
    if (controller === reportController) querying.value = false
  }
}
onBeforeUnmount(() => { reportController?.abort(); reportController = undefined })
</script>

<template>
  <section class="pdm-report-panel" :aria-label="region.section.title">
    <header class="pdm-report-panel__heading"><h3>{{ region.section.title }}</h3><NButton :aria-label="`筛选${region.section.title}`" @click="openFilters">筛选</NButton></header>
    <ReportDataStatus :meta="region.meta" :busy="querying" @refresh="query(region.filters, 'refresh')" />
    <NAlert v-if="queryError && !visible" type="error" title="统计更新未完成">{{ queryError }}，原表格与图表已保留。</NAlert>
    <NAlert v-if="region.omittedGroups" type="warning" title="部分文档未生成分组表">本次所选范围另有 {{ region.omittedGroups }} 份文档未对应有效项目或固定专业，已计入顶部全部数据卡片。</NAlert>
    <ReportPanelState v-if="!region.panels.length" :type="querying ? 'loading' : 'empty'"
      :title="querying ? '正在读取本表数据' : region.queried ? '当前筛选范围没有可展示的分表' : '请选择条件后查询本表'"
      :message="region.section.groupBy === 'project' ? '选择项目后，每个项目分别生成一张按专业展开的表。' : region.section.groupBy === 'profession' ? '选择专业后，每个专业分别生成一张按项目展开的表。' : '可以调整筛选条件后重新查询。'" />
    <PdmChartTable v-for="panel in region.panels" :key="panel.table.key" :panel="panel" :loading="querying" />
    <NModal v-model:show="visible" preset="card" :title="`${region.section.title} · 筛选`" style="width:min(880px,calc(100vw - 64px));max-height:calc(100vh - 96px)"
      :content-style="{ maxHeight: 'calc(100vh - 224px)', overflowY: 'auto' }" :mask-closable="!querying" :close-on-esc="!querying" :closable="!querying" aria-label="本表筛选">
      <NAlert v-if="queryError" type="error" title="查询失败">{{ queryError }}，原表格与图表已保留。</NAlert>
      <PdmFilterBar v-model="draft" :kind="region.section.kind" :fixed-group-by="region.section.groupBy" :options="options" :status="status" :error="error" :querying="querying" @submit="value => query(value, queryError ? 'refresh' : 'read')" @retry="retry">
        <template #actions><NButton size="large" :disabled="querying" @click="visible = false">取消</NButton></template>
      </PdmFilterBar>
      <p class="pdm-report-panel__basis">{{ draft.dateBasis === 'actual' ? '按实际日期筛选，实际日期为空时回退到计划日期。' : '按计划日期筛选，计划日期为空时回退到实际日期。' }}仅有实际日期的文档计为按时完成；两个日期都为空的文档，每个区间均计入总数和未完成数。</p>
    </NModal>
  </section>
</template>

<style scoped>
.pdm-report-panel{display:grid;gap:var(--space-4);min-width:0}
.pdm-report-panel__heading{display:flex;align-items:center;justify-content:space-between;gap:var(--space-4)}
.pdm-report-panel__heading h3{margin:0;color:var(--text-color-1);font-size:var(--font-section-title);font-weight:600}
.pdm-report-panel :deep(.report-trend-panel){margin-bottom:0}
.pdm-report-panel__basis{margin:var(--space-3) 0 0;color:var(--text-color-3);font-size:var(--font-auxiliary)}
</style>
