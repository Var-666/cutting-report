<script setup lang="ts" generic="T extends object">
import type { PropType, VNode } from 'vue'
import type { DataTableBaseColumn, DataTableColumns, DataTableCreateSummary } from 'naive-ui'

import { computed, h, onBeforeUnmount, onMounted, onUpdated, ref, watch } from 'vue'
import { NButton, NDataTable, NPagination } from 'naive-ui'

const props = defineProps({
  items: { type: Array as PropType<T[]>, default: () => [] },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  loading: Boolean,
  autoFit: { type: Boolean, default: true },
  paginate: { type: Boolean, default: true },
  columns: { type: Array as PropType<readonly DataTableBaseColumn<T>[]>, required: true },
  rowKey: { type: Function as PropType<(row: T) => string | number>, required: true },
  rowClassName: { type: Function as PropType<(row: T, index: number) => string> },
  summary: Boolean,
  flexHeight: Boolean,
  maxHeight: { type: Number as PropType<number | null>, default: 540 },
  pageSizes: { type: Array as PropType<number[]>, default: () => [20, 50, 100] },
})
const emit = defineEmits<{ page: [page: number]; 'page-size': [size: number] }>()
const slots = defineSlots<{
  [name: `c${number}`]: (props: { row: T; index: number }) => VNode[]
} & {
  [name: `header-c${number}` | `summary-c${number}`]: () => VNode[]
} & { empty?: () => VNode[] }>()
const root = ref<HTMLElement>()
const fittedWidths = ref<Record<string, number>>({})
const resizedWidths = ref<Record<string, number>>({})
const rowRevision = ref(0)
// Naive's opened tooltip can retain its old trigger slot when a row is replaced.
watch(() => props.items, () => { rowRevision.value += 1 })
function fitColumns() {
  if (!props.autoFit || !root.value || props.loading) return
  const widths: Record<string, number> = {}
  for (const content of root.value.querySelectorAll<HTMLElement>('[data-fit-column]')) {
    const cell = content.closest('td,th')
    if (!cell) continue
    const style = getComputedStyle(cell)
    const key = content.dataset.fitColumn!
    const width = Math.ceil(content.getBoundingClientRect().width + parseFloat(style.paddingLeft) + parseFloat(style.paddingRight) + 2)
    widths[key] = Math.max(widths[key] || 0, content.classList.contains('app-report-table-cell--controls') ? width : Math.min(360, width))
  }
  if (JSON.stringify(widths) !== JSON.stringify(fittedWidths.value)) fittedWidths.value = widths
}
let observer: ResizeObserver | undefined
onMounted(() => {
  fitColumns()
  if (props.autoFit && root.value) {
    observer = new ResizeObserver(fitColumns)
    observer.observe(root.value)
  }
})
onUpdated(fitColumns)
onBeforeUnmount(() => observer?.disconnect())
function rememberColumnWidth(_width: number, width: number, column: DataTableBaseColumn) {
  if (props.autoFit) resizedWidths.value = { ...resizedWidths.value, [column.key]: width }
}
const columns = computed<DataTableColumns<T>>(() => props.columns.map(column => {
  const ellipsis = typeof column.ellipsis === 'object' ? column.ellipsis : {}
  return {
    resizable: column.ellipsis !== false,
    ...column,
    className: [column.className, resizedWidths.value[column.key] ? 'app-report-table-column--resized' : ''].filter(Boolean).join(' '),
    ellipsis: column.ellipsis === false || column.ellipsis === true ? column.ellipsis : {
      ...ellipsis,
      tooltip: ellipsis.tooltip === false ? false : {
        key: rowRevision.value,
        style: { maxWidth: 'min(640px, calc(100vw - 32px))', whiteSpace: 'normal', overflowWrap: 'anywhere' },
        ...(typeof ellipsis.tooltip === 'object' ? ellipsis.tooltip : {}),
      },
    },
    ...(props.autoFit ? { width: resizedWidths.value[column.key] ?? fittedWidths.value[column.key] ?? 80, minWidth: undefined, maxWidth: undefined } : {}),
    titleAlign: column.align || 'left',
    title: props.autoFit ? (titleColumn: DataTableBaseColumn) => h('span', { 'data-fit-column': column.key }, slots[`header-${column.key}` as `header-c${number}`]?.() ?? (typeof column.title === 'function' ? column.title(titleColumn) : column.title)) : slots[`header-${column.key}` as `header-c${number}`] ? () => slots[`header-${column.key}` as `header-c${number}`]!() : column.title,
    render: slots[column.key as `c${number}`] || column.render ? (row: T, index: number) => h('span', {
      class: ['app-report-table-cell', { 'app-report-table-cell--controls': column.ellipsis === false }],
      'data-fit-column': props.autoFit ? column.key : undefined,
    }, slots[column.key as `c${number}`]?.({ row, index }) ?? column.render?.(row, index)) : column.render,
  }
}))
const tableWidth = computed(() => columns.value.reduce((sum, column) => sum + Number(column.width ?? column.minWidth ?? 128), 0))
const createSummary: DataTableCreateSummary<T> = () => [Object.fromEntries(columns.value.map(column => {
  const key = 'key' in column ? column.key : ''
  return [key, { value: h('span', { class: 'app-report-table-cell', 'data-fit-column': props.autoFit ? key : undefined }, slots[`summary-${key}` as `summary-c${number}`]?.()) }]
}))]
</script>
<template>
  <div ref="root" role="group" class="app-report-table" :class="{ 'app-report-table--flex': flexHeight, 'app-report-table--auto-fit': autoFit }">
    <NDataTable table-layout="fixed" remote size="small" :columns="columns" :data="items" :row-key="rowKey" :row-class-name="rowClassName" :summary="summary ? createSummary : undefined" :loading="loading" :scroll-x="tableWidth" :max-height="flexHeight ? undefined : maxHeight ?? undefined" :flex-height="flexHeight" :single-line="false" :pagination="false" @unstable-column-resize="rememberColumnWidth"><template #empty><slot name="empty">暂无数据</slot></template></NDataTable>
    <div v-if="paginate" class="app-report-pagination"><span>共 {{ total }} 条</span><NPagination :page="page" :page-size="pageSize" :item-count="total" :page-slot="5" :page-sizes="pageSizes" :show-size-picker="pageSizes.length > 0" :disabled="loading" @update:page="emit('page', $event)" @update:page-size="emit('page-size', $event)">
      <template #prev><NButton class="pagination-control" text attr-type="button" aria-label="上一页" :disabled="loading || page <= 1">‹</NButton></template>
      <template #next><NButton class="pagination-control" text attr-type="button" aria-label="下一页" :disabled="loading || page >= Math.max(1, Math.ceil(total / pageSize))">›</NButton></template>
      <template #label="{ type, node, active }"><NButton class="pagination-control" text attr-type="button" :aria-label="type === 'page' ? `第 ${node} 页` : type === 'fast-forward' ? '向后翻页' : '向前翻页'" :aria-current="type === 'page' && active ? 'page' : undefined" :disabled="loading">{{ type === 'page' ? node : '…' }}</NButton></template>
    </NPagination></div>
  </div>
</template>
<style scoped>
:deep(.n-data-table-td),:deep(.n-data-table-th){white-space:nowrap}
:deep(.app-report-table-cell){display:inline-flex;align-items:center;gap:var(--space-2);vertical-align:middle;white-space:nowrap}
:deep(.app-report-table-cell--controls){max-width:100%}
.app-report-table--auto-fit :deep([data-fit-column]){display:inline-flex;width:max-content;max-width:none}
.app-report-table--auto-fit :deep(td:not(.app-report-table-column--resized) .n-ellipsis){max-width:min(100%,330px)}
.app-report-table{width:100%;min-width:0}.app-report-table--flex{height:100%;display:flex;flex-direction:column}.app-report-table--flex :deep(.n-data-table){flex:1;min-height:0}.pagination-control{width:100%;height:100%;min-width:0;padding:0}.pagination-control:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:2px}:deep(.n-data-table-tr.selected .n-data-table-td){background:color-mix(in srgb, rgb(var(--primary-color)) 8%, var(--n-td-color))}:deep(.n-data-table-tr.invalid .n-data-table-td){background:color-mix(in srgb, rgb(var(--error-color)) 8%, var(--n-td-color))}.app-report-pagination{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);padding:14px var(--space-4);flex-wrap:wrap;color:var(--text-color-2);font-size:var(--font-table-head)}:deep(.n-data-table-td){height:var(--table-row-height);font-size:var(--font-auxiliary);font-variant-numeric:tabular-nums;vertical-align:middle;padding:var(--table-cell-padding);border-right-color:color-mix(in srgb, var(--n-merged-border-color) 35%, transparent);}:deep(.n-data-table-th){font-size:var(--font-table-head);vertical-align:middle;padding:var(--table-cell-padding);border-right-color:color-mix(in srgb, var(--n-merged-border-color) 35%, transparent);}:deep(.n-data-table-td--summary){position:sticky;bottom:0;z-index:2;background:var(--n-td-color);font-weight:600}:deep(.n-data-table-td--summary.n-data-table-td--fixed-left){z-index:3}@media(max-width:600px){.app-report-table.welding-table-wrap :deep(.n-data-table-td--fixed-left),.app-report-table.welding-table-wrap :deep(.n-data-table-th--fixed-left){position:static!important}.app-report-table :deep(.n-data-table-td--fixed-right),.app-report-table :deep(.n-data-table-th--fixed-right){position:static!important}.app-report-pagination{align-items:flex-start;flex-direction:column}:deep(.n-pagination){flex-wrap:wrap}}
</style>
