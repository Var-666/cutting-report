<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { PdmSummaryTable } from '../../api/generated/types.gen'
import { unassignedPdmRows } from '../../utils/pdmReport'

const props = defineProps<{ table: PdmSummaryTable; startDate: string; endDate: string; queried: boolean }>()
const element = ref<HTMLTableElement | null>(null)
const widths = ref<number[]>([])
let stopResize: (() => void) | undefined
const labelColumns = computed(() => props.table.group_label ? 2 : 1)
const columnCount = computed(() => labelColumns.value + 5)
const unassignedTotal = computed(() => unassignedPdmRows(props.table))
const rows = computed(() => props.table.rows.map((row, index, all) => ({ ...row,
  firstInCategory: index === 0 || all[index - 1].category !== row.category,
  categorySpan: all.filter(item => item.category === row.category).length,
})))
function rate(value: number | null) { return value === null ? '—' : `${value.toFixed(2)}%` }
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
  <div class="pdm-summary-wrap">
    <table ref="element" class="pdm-summary" :aria-label="`${table.title} · ${table.scope_label}`" :style="widths.length ? { width: `${widths.reduce((sum, width) => sum + width, 0)}px` } : undefined">
      <colgroup><col v-for="index in columnCount" :key="index" :class="{ 'pdm-summary__category': index === 1, 'pdm-summary__group': index === 2 && table.group_label }" :style="widths[index - 1] ? { width: `${widths[index - 1]}px` } : undefined"></colgroup>
      <thead>
        <tr class="pdm-summary__title"><th :colspan="labelColumns + 2">{{ table.title }}</th><th colspan="2">{{ period }}</th><th>{{ table.scope_label }}</th></tr>
        <tr><th :colspan="labelColumns" rowspan="2"><span class="pdm-summary__sr-label">{{ table.group_label ? `资料类型及${table.group_label}` : '资料类型' }}</span><span v-for="index in labelColumns" :key="index" class="pdm-summary__resize" :style="{ right: `${(labelColumns - index) / labelColumns * 100}%` }" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="`调整第${index}列宽度`" @pointerdown="resize($event, index - 1)" @keydown="resizeKey($event, index - 1)" /></th><th v-for="(label, index) in ['图纸总数', '已完成数', '按计划完成数']" :key="label" rowspan="2">{{ label }}<span class="pdm-summary__resize" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="`调整${label}列宽度`" @pointerdown="resize($event, labelColumns + index)" @keydown="resizeKey($event, labelColumns + index)" /></th><th colspan="2" scope="colgroup">完成率</th></tr>
        <tr><th v-for="(label, index) in ['按时完成率', '总完成率']" :key="label" scope="col">{{ label }}<span class="pdm-summary__resize" role="separator" tabindex="0" aria-orientation="vertical" :aria-label="`调整${label}列宽度`" @pointerdown="resize($event, labelColumns + 3 + index)" @keydown="resizeKey($event, labelColumns + 3 + index)" /></th></tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in rows" :key="`${row.category}-${row.group_id}-${index}`" :class="{ 'pdm-summary__subtotal': row.subtotal }">
          <th v-if="row.firstInCategory" :rowspan="row.categorySpan" scope="rowgroup" class="pdm-summary__type">{{ row.category }}</th>
          <th v-if="table.group_label" scope="row" class="pdm-summary__name"><span :title="row.group_name">{{ row.group_name }}</span></th>
          <td>{{ row.total }}</td><td>{{ row.completed }}</td><td>{{ row.on_time }}</td><td>{{ rate(row.on_time_rate) }}</td><td>{{ rate(row.total_rate) }}</td>
        </tr>
        <tr v-if="!rows.length"><td :colspan="columnCount" class="pdm-summary__empty">{{ queried ? '当前筛选条件下暂无数据' : '选择时间段及筛选条件后查询' }}</td></tr>
      </tbody>
      <tfoot v-if="queried && !table.group_label"><tr><th scope="row">总计</th><td>{{ table.totals.total }}</td><td>{{ table.totals.completed }}</td><td>{{ table.totals.on_time }}</td><td>{{ rate(table.totals.on_time_rate) }}</td><td>{{ rate(table.totals.total_rate) }}</td></tr></tfoot>
    </table>
    <p v-if="queried && unassignedTotal > 0" class="pdm-summary__note" role="note">有 {{ unassignedTotal }} 份文档{{ table.group_label === '专业' ? '的专业未对应固定的八项专业' : '的项目信息缺失' }}，已计入总计，未分配到{{ table.group_label }}行。</p>
  </div>
</template>

<style scoped>
/* The supplied report requires merged, multi-level headers and vertical category groups. */
.pdm-summary-wrap{min-width:0;overflow-x:auto;scrollbar-width:none;background:rgb(var(--container-bg-color))}
.pdm-summary-wrap::-webkit-scrollbar{display:none}
.pdm-summary{width:100%;table-layout:fixed;border-collapse:collapse;color:var(--text-color-1);font-size:var(--font-body);font-variant-numeric:tabular-nums}
.pdm-summary th,.pdm-summary td{height:36px;padding:var(--space-2) var(--space-3);border:1px solid var(--theme-border-color);text-align:center;font-weight:400;vertical-align:middle}
.pdm-summary thead th{position:relative;background:var(--theme-table-header-color);font-weight:500}
.pdm-summary__title th{height:48px;background:rgb(var(--container-bg-color));font-size:var(--font-body)}
.pdm-summary__category{width:16%}.pdm-summary__group{width:20%}
.pdm-summary__type{overflow-wrap:anywhere;white-space:normal}
.pdm-summary__name span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pdm-summary td{white-space:nowrap}
.pdm-summary__subtotal td,.pdm-summary__subtotal .pdm-summary__name,.pdm-summary tfoot th,.pdm-summary tfoot td{font-weight:600}
.pdm-summary td.pdm-summary__empty{height:110px;color:var(--text-color-3)}
.pdm-summary__note{margin:0;padding:var(--space-3);color:var(--text-color-2);font-size:var(--font-body)}
.pdm-summary__resize{position:absolute;right:-4px;top:0;bottom:0;z-index:1;width:8px;cursor:col-resize;touch-action:none}
.pdm-summary__resize:focus-visible{outline:2px solid rgb(var(--primary-color))}
.pdm-summary__sr-label{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
</style>
