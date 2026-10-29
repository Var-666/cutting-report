<script setup lang="ts" generic="T extends object">
import type { PropType, VNode } from 'vue'
import type { DataTableBaseColumn, DataTableColumns, DataTableCreateSummary } from 'naive-ui'

import { computed } from 'vue'
import { NButton, NDataTable, NPagination } from 'naive-ui'

const props = defineProps({
  items: { type: Array as PropType<T[]>, default: () => [] },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  loading: Boolean,
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
const columns = computed<DataTableColumns<T>>(() => props.columns.map(column => ({
  ellipsis: { tooltip: true },
  ...column,
  titleAlign: column.align || 'left',
  title: slots[`header-${column.key}` as `header-c${number}`] ? () => slots[`header-${column.key}` as `header-c${number}`]!() : column.title,
  render: slots[column.key as `c${number}`] ? (row: T, index: number) => slots[column.key as `c${number}`]!({ row, index }) : column.render,
})))
const tableWidth = computed(() => columns.value.reduce((sum, column) => sum + Number(column.width ?? column.minWidth ?? 128), 0))
const createSummary: DataTableCreateSummary<T> = () => [Object.fromEntries(columns.value.map(column => {
  const key = 'key' in column ? column.key : ''
  return [key, { value: slots[`summary-${key}` as `summary-c${number}`]?.() }]
}))]
</script>
<template>
  <div role="group" class="app-report-table" :class="{ 'app-report-table--flex': flexHeight }">
    <NDataTable table-layout="fixed" remote size="small" :columns="columns" :data="items" :row-key="rowKey" :row-class-name="rowClassName" :summary="summary ? createSummary : undefined" :loading="loading" :scroll-x="tableWidth" :max-height="flexHeight ? undefined : maxHeight ?? undefined" :flex-height="flexHeight" :single-line="false" :pagination="false"><template #empty><slot name="empty">暂无数据</slot></template></NDataTable>
    <div v-if="paginate" class="app-report-pagination"><span>共 {{ total }} 条</span><NPagination :page="page" :page-size="pageSize" :item-count="total" :page-slot="5" :page-sizes="pageSizes" :show-size-picker="pageSizes.length > 0" :disabled="loading" @update:page="emit('page', $event)" @update:page-size="emit('page-size', $event)">
      <template #prev><NButton class="pagination-control" text attr-type="button" aria-label="上一页" :disabled="loading || page <= 1">‹</NButton></template>
      <template #next><NButton class="pagination-control" text attr-type="button" aria-label="下一页" :disabled="loading || page >= Math.max(1, Math.ceil(total / pageSize))">›</NButton></template>
      <template #label="{ type, node, active }"><NButton class="pagination-control" text attr-type="button" :aria-label="type === 'page' ? `第 ${node} 页` : type === 'fast-forward' ? '向后翻页' : '向前翻页'" :aria-current="type === 'page' && active ? 'page' : undefined" :disabled="loading">{{ type === 'page' ? node : '…' }}</NButton></template>
    </NPagination></div>
  </div>
</template>
<style scoped>
.app-report-table{width:100%;min-width:0}.app-report-table--flex{height:100%;display:flex;flex-direction:column}.app-report-table--flex :deep(.n-data-table){flex:1;min-height:0}.pagination-control{width:100%;height:100%;min-width:0;padding:0}.pagination-control:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:2px}:deep(.n-data-table-tr.selected .n-data-table-td){background:rgb(var(--primary-color) / .08)}:deep(.n-data-table-tr.invalid .n-data-table-td){background:rgb(var(--error-color) / .08)}.app-report-pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;flex-wrap:wrap;color:var(--text-color-3);font-size:12px}:deep(.n-data-table-td){height:44px;font-size:12px;font-variant-numeric:tabular-nums;vertical-align:middle;padding:6px 14px;border-right-color:color-mix(in srgb, var(--n-merged-border-color) 35%, transparent);}:deep(.n-data-table-th){font-size:12px;vertical-align:middle;padding:6px 14px;border-right-color:color-mix(in srgb, var(--n-merged-border-color) 35%, transparent);}:deep(.n-data-table-td--summary){position:sticky;bottom:0;z-index:2;background:var(--n-td-color);font-weight:600}:deep(.n-data-table-td--summary.n-data-table-td--fixed-left){z-index:3}@media(max-width:600px){.app-report-table:not(.welding-table-wrap) :deep(.n-data-table-td--fixed-right),.app-report-table:not(.welding-table-wrap) :deep(.n-data-table-th--fixed-right){position:static!important}.app-report-pagination{align-items:flex-start;flex-direction:column}:deep(.n-pagination){flex-wrap:wrap}}
</style>
