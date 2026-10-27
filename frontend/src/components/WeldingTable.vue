<script setup lang="ts">
import type { PropType } from 'vue'
import type { DataTableBaseColumn, DataTableColumns } from 'naive-ui'
import type { DataRow } from '../types/api'
import type { WeldingJobRow, WeldingPartRow, WeldingRow } from '../types/welding'

import { computed, h } from 'vue'
import { NButton, NDataTable, NPagination } from 'naive-ui'
import { weldingColumns, weldingDisplay } from '../utils/weldingReport.ts'

const props = defineProps({ kind: { type: String, required: true }, items: { type: Array as PropType<WeldingRow[]>, default: () => [] }, total: { type: Number, default: 0 }, page: { type: Number, default: 1 }, pageSize: { type: Number, default: 20 }, loading: Boolean, paginate: { type: Boolean, default: true } })
const emit = defineEmits<{ page: [page: number]; 'page-size': [size: number]; detail: [row: WeldingJobRow | WeldingPartRow]; copy: [value: string] }>()
const columns = computed(() => {
  const values: DataTableColumns<WeldingRow> = weldingColumns[props.kind].map(([key, title], index): DataTableBaseColumn<WeldingRow> => ({ key, title, width: /serial|time|quality/.test(key) ? 210 : 145, fixed: index === 0 ? 'left' : undefined, align: /length|hours|count|output|speed/.test(key) ? 'right' : 'left', ellipsis: { tooltip: true },
    render: row => { const value = (row as unknown as DataRow)[key]; return key === 'serial_number' ? h(NButton, { text: true, title: `复制：${value || ''}`, onClick: () => emit('copy', String(value || '')) }, { default: () => weldingDisplay(value, key) }) : weldingDisplay(value, key) },
  }))
  if (props.kind === 'parts' || props.kind === 'jobs') values.push({ key: 'actions', title: '详情', width: 120, fixed: 'right', render: row => 'weld_type_label' in row || 'project_no' in row ? h(NButton, { size: 'small', onClick: () => emit('detail', row) }, { default: () => props.kind === 'parts' ? '工件与焊缝' : '作业详情' }) : null })
  return values
})
const rowKey = (row: WeldingRow) => 'id' in row ? row.id : 'production_date' in row ? row.production_date : `${row.device_no}:${row.robot_no}`
</script>
<template>
  <div class="welding-table-wrap">
    <NDataTable remote size="small" :columns="columns" :data="items" :row-key="rowKey" :loading="loading" :scroll-x="columns.reduce((sum, column) => sum + Number(column.width), 0)" :max-height="540" :single-line="false" :pagination="false" />
    <div v-if="paginate" class="welding-pagination"><span>共 {{ total }} 条</span><NPagination :page="page" :page-size="pageSize" :item-count="total" :page-slot="5" :page-sizes="[20, 50, 100]" show-size-picker :disabled="loading" @update:page="emit('page', $event)" @update:page-size="emit('page-size', $event)" /></div>
  </div>
</template>
<style scoped>
.welding-table-wrap{min-width:0}.welding-pagination{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 0;flex-wrap:wrap;color:var(--text-color-3);font-size:12px}:deep(.n-data-table-td){height:44px;font-size:12px}:deep(.n-data-table-th){font-size:12px}@media(max-width:600px){.welding-pagination{align-items:flex-start;flex-direction:column}:deep(.n-pagination){flex-wrap:wrap}}
</style>
