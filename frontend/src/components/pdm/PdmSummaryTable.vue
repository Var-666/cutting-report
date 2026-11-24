<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { PdmSummaryTable } from '../../api/generated/types.gen'
import { formatPdmRate, pdmInitialProjectCount, unassignedPdmRows } from '../../utils/pdmReport'

const props = defineProps<{ table: PdmSummaryTable; startDate: string; endDate: string; queried: boolean; selectedProjects?: ReadonlySet<string> }>()
const emit = defineEmits<{ select: [id: string] }>()
const element = ref<HTMLTableElement | null>(null)
const widths = ref<number[]>([])
const projectWindowHeight = ref<number>()
watch([element, () => props.table.group_label], ([table, group], _previous, onCleanup) => {
  projectWindowHeight.value = undefined
  if (!table || group !== '项目') return
  const measure = () => {
    const body = table.tBodies.item(0)
    const next = body?.rows.item(pdmInitialProjectCount)
    if (!body || !next || next.classList.contains('pdm-summary__subtotal')) {
      projectWindowHeight.value = undefined
      return
    }
    const subtotal = body.querySelector<HTMLTableRowElement>('.pdm-summary__subtotal:last-child')
    const visibleRowsHeight = Array.from(body.rows).slice(0, pdmInitialProjectCount)
      .reduce((height, row) => height + row.offsetHeight, 0)
    projectWindowHeight.value = (table.tHead?.offsetHeight ?? 0) + visibleRowsHeight + (subtotal?.offsetHeight ?? 0)
  }
  const observer = new ResizeObserver(measure)
  observer.observe(table)
  measure()
  onCleanup(() => observer.disconnect())
}, { flush: 'post' })
let stopResize: (() => void) | undefined
const labelColumns = computed(() => props.table.group_label ? 2 : 1)
const selectProject = computed(() => Boolean(props.selectedProjects))
const selectionColumns = computed(() => Number(selectProject.value))
const columnCount = computed(() => selectionColumns.value + labelColumns.value + 5)
const unassignedTotal = computed(() => unassignedPdmRows(props.table))
const rows = computed(() => {
  const spans = new Map<string, number>()
  for (const row of props.table.rows) spans.set(row.category, (spans.get(row.category) || 0) + 1)
  return props.table.rows.map((row, index, all) => ({
    row, firstInCategory: index === 0 || all[index - 1].category !== row.category,
    categorySpan: spans.get(row.category),
  }))
})
const period = computed(() => props.startDate && props.endDate ? `${props.startDate} ～ ${props.endDate}` : '请选择统计时间段')
function measureWidths() {
  if (!widths.value.length && element.value) widths.value = Array.from(element.value.querySelectorAll('col'), col => col.getBoundingClientRect().width)
}
function resize(event: PointerEvent, index: number) {
  event.preventDefault()
  stopResize?.()
  measureWidths()
  const origin = event.clientX, originalWidth = widths.value[index]
  const move = (next: PointerEvent) => { widths.value[index] = Math.max(64, originalWidth + next.clientX - origin) }
  stopResize = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop); stopResize = undefined }
  const stop = () => stopResize?.()
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', stop, { once: true })
}
function resizeKey(event: KeyboardEvent, index: number) {
  if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return
  event.preventDefault()
  measureWidths()
  widths.value[index] = Math.max(64, widths.value[index] + (event.key === 'ArrowLeft' ? -16 : 16))
}
onBeforeUnmount(() => stopResize?.())
</script>

<template>
  <div class="pdm-summary-wrap" :class="{ 'pdm-summary-wrap--projects': table.group_label === '项目' }"
    role="region" :aria-label="`${table.scope_label}表格滚动区域`" tabindex="0"
    :style="projectWindowHeight ? { '--pdm-project-window-height': `${projectWindowHeight}px` } : undefined">
    <table ref="element" class="pdm-summary" :aria-label="`${table.title} · ${table.scope_label}`" :style="widths.length ? { width: `${widths.reduce((sum, width) => sum + width, 0)}px` } : undefined">
      <colgroup><col v-for="index in columnCount" :key="index" :class="{ 'pdm-summary__category': index === 1, 'pdm-summary__selection': selectProject && index === 2, 'pdm-summary__group': index === selectionColumns + 2 && table.group_label }" :style="widths[index - 1] ? { width: `${widths[index - 1]}px` } : undefined"></colgroup>
      <thead>
        <tr class="pdm-summary__title"><th :colspan="selectionColumns + labelColumns + 2">{{ table.title }}</th><th colspan="2">{{ period }}</th><th>{{ table.scope_label }}</th></tr>
        <tr>
          <th rowspan="2" scope="col">资料类型<span class="pdm-summary__resize" role="separator" tabindex="0" aria-orientation="vertical" aria-label="调整资料类型列宽度" @pointerdown="resize($event, 0)" @keydown="resizeKey($event, 0)" /></th>
          <th v-if="selectProject" rowspan="2" scope="col" class="pdm-summary__select">选择</th>
          <th v-if="table.group_label" rowspan="2" scope="col">{{ table.group_label }}<span class="pdm-summary__resize" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="`调整${table.group_label}列宽度`" @pointerdown="resize($event, 1 + selectionColumns)" @keydown="resizeKey($event, 1 + selectionColumns)" /></th>
          <th v-for="(label, index) in ['图纸总数', '已完成数', '按计划完成数']" :key="label" rowspan="2" scope="col">{{ label }}<span class="pdm-summary__resize" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="`调整${label}列宽度`" @pointerdown="resize($event, selectionColumns + labelColumns + index)" @keydown="resizeKey($event, selectionColumns + labelColumns + index)" /></th>
          <th colspan="2" scope="colgroup">完成率</th>
        </tr>
        <tr><th v-for="(label, index) in ['按时完成率', '总完成率']" :key="label" scope="col">{{ label }}<span class="pdm-summary__resize" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="`调整${label}列宽度`" @pointerdown="resize($event, selectionColumns + labelColumns + 3 + index)" @keydown="resizeKey($event, selectionColumns + labelColumns + 3 + index)" /></th></tr>
      </thead>
      <tbody>
        <tr v-for="({ row, firstInCategory, categorySpan }, index) in rows" :key="`${row.category}-${row.group_id}-${index}`"
          :class="{ 'pdm-summary__subtotal': row.subtotal, 'pdm-summary__selected': selectedProjects?.has(row.group_id ?? '') }">
          <th v-if="firstInCategory" :rowspan="categorySpan" scope="rowgroup" :data-category="row.category" class="pdm-summary__type"><span>{{ row.category }}</span></th>
          <td v-if="selectProject" class="pdm-summary__select">
            <input v-if="!row.subtotal && row.group_id" type="checkbox" :checked="selectedProjects?.has(row.group_id ?? '')"
              :aria-label="`选择${row.group_name}（${row.category}）`" @change="emit('select', row.group_id)" />
          </td>
          <th v-if="table.group_label" scope="row" class="pdm-summary__name"><span :title="row.group_name">{{ row.group_name }}</span></th>
          <td>{{ row.total }}</td><td>{{ row.completed }}</td><td>{{ row.on_time }}</td><td>{{ formatPdmRate(row.on_time_rate) }}</td><td>{{ formatPdmRate(row.total_rate) }}</td>
        </tr>
        <tr v-if="!rows.length"><td :colspan="columnCount" class="pdm-summary__empty">{{ queried ? '当前筛选条件下暂无数据' : '选择时间段及筛选条件后查询' }}</td></tr>
      </tbody>
      <tfoot v-if="queried && !table.group_label"><tr><th scope="row">总计</th><td>{{ table.totals.total }}</td><td>{{ table.totals.completed }}</td><td>{{ table.totals.on_time }}</td><td>{{ formatPdmRate(table.totals.on_time_rate) }}</td><td>{{ formatPdmRate(table.totals.total_rate) }}</td></tr></tfoot>
    </table>
    <p v-if="queried && unassignedTotal > 0" class="pdm-summary__note" role="note">有 {{ unassignedTotal }} 份文档{{ table.group_label === '专业' ? '的专业未对应固定的八项专业' : '的项目信息缺失' }}，已计入总计，未分配到{{ table.group_label }}行。</p>
  </div>
</template>

<style scoped>
/* The supplied report requires merged, multi-level headers and vertical category groups. */
.pdm-summary-wrap{min-width:0;overflow:auto;background:rgb(var(--container-bg-color))}
.pdm-summary-wrap--projects{max-height:min(60dvh,560px,var(--pdm-project-window-height,560px));overscroll-behavior-y:contain;scroll-padding-bottom:40px;scrollbar-width:none}
.pdm-summary-wrap--projects .pdm-summary{border-collapse:separate;border-spacing:0;border-inline-start:1px solid var(--theme-border-color)}
.pdm-summary-wrap--projects .pdm-summary :is(th,td){border-width:0 1px 1px 0}
.pdm-summary-wrap--projects .pdm-summary__type{vertical-align:top;background:rgb(var(--container-bg-color))}
.pdm-summary-wrap--projects .pdm-summary__type > span{display:block;position:sticky;top:58%}
.pdm-summary-wrap--projects thead{position:sticky;top:0;z-index:2;background:var(--theme-table-header-color);box-shadow:inset 0 1px var(--theme-border-color)}
.pdm-summary-wrap--projects tbody > .pdm-summary__subtotal:last-child > :is(th,td){
  position:sticky;bottom:0;z-index:1;background:var(--theme-table-header-color);box-shadow:0 -1px var(--theme-border-color)
}
.pdm-summary-wrap:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:-2px}
.pdm-summary{min-width:560px;width:100%;table-layout:fixed;border-collapse:collapse;color:var(--text-color-1);font-size:var(--font-body);font-variant-numeric:tabular-nums}
.pdm-summary th,.pdm-summary td{height:36px;padding:var(--space-2) var(--space-3);border:1px solid var(--theme-border-color);text-align:center;font-weight:400;vertical-align:middle}
.pdm-summary thead th{position:relative;background:var(--theme-table-header-color);font-weight:500}
.pdm-summary__title th{height:48px;background:rgb(var(--container-bg-color));font-size:var(--font-body)}
.pdm-summary__selection{width:40px}
.pdm-summary .pdm-summary__select{padding:var(--space-1)}
.pdm-summary__select input{width:16px;height:16px;vertical-align:middle;accent-color:rgb(var(--primary-color))}
.pdm-summary__selected{background:rgb(var(--primary-color) / .08)}
.pdm-summary__category{width:16%}.pdm-summary__group{width:20%}
.pdm-summary__type{overflow-wrap:anywhere;white-space:normal}
.pdm-summary__name span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pdm-summary td{white-space:nowrap}
.pdm-summary__subtotal td,.pdm-summary__subtotal .pdm-summary__name,.pdm-summary tfoot th,.pdm-summary tfoot td{font-weight:600}
.pdm-summary td.pdm-summary__empty{height:110px;color:var(--text-color-3)}
.pdm-summary__note{margin:0;padding:var(--space-3);color:var(--text-color-2);font-size:var(--font-body)}
.pdm-summary__resize{position:absolute;right:-4px;top:0;bottom:0;z-index:1;width:8px;cursor:col-resize;touch-action:none}
.pdm-summary thead tr:last-child th:last-child .pdm-summary__resize{right:0}
.pdm-summary__resize:focus-visible{outline:2px solid rgb(var(--primary-color))}
</style>
