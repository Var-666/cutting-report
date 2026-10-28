<script setup lang="ts">
import type { PropType } from 'vue'
import type { DataTableBaseColumn } from 'naive-ui'
import type { DataRow } from '../types/api'
import type { WeldingJobRow, WeldingPartRow, WeldingRow } from '../types/welding'
import { computed, h } from 'vue'
import { NButton } from 'naive-ui'
import AppReportTable from './AppReportTable.vue'
import { weldingColumns, weldingColumnLayout, weldingDisplay } from '../utils/weldingReport.ts'

const props = defineProps({
  kind: { type: String, required: true },
  items: { type: Array as PropType<WeldingRow[]>, default: () => [] },
  total: { type: Number, default: 0 },
  page: { type: Number, default: 1 },
  pageSize: { type: Number, default: 20 },
  loading: Boolean,
  paginate: { type: Boolean, default: true },
})
const emit = defineEmits<{ page: [page: number]; 'page-size': [size: number]; detail: [row: WeldingJobRow | WeldingPartRow]; copy: [value: string] }>()
const columns = computed(() => {
  const values: DataTableBaseColumn<WeldingRow>[] = weldingColumns[props.kind].map(([key, title], index): DataTableBaseColumn<WeldingRow> => ({ key, title, ...weldingColumnLayout[key], fixed: index === 0 ? 'left' : undefined, titleAlign: weldingColumnLayout[key].align, ellipsis: { tooltip: true },
    render: row => { const value = (row as unknown as DataRow)[key]; return key === 'serial_number' ? h(NButton, { text: true, title: `复制：${value || ''}`, onClick: () => emit('copy', String(value || '')) }, { default: () => weldingDisplay(value, key) }) : weldingDisplay(value, key) },
  }))
  if (props.kind === 'parts' || props.kind === 'jobs') values.push({ key: 'actions', title: '详情', width: 120, fixed: 'right', align: 'center', titleAlign: 'center', render: row => 'weld_type_label' in row || 'project_no' in row ? h(NButton, { size: 'small', onClick: () => emit('detail', row as unknown as WeldingJobRow | WeldingPartRow) }, { default: () => props.kind === 'parts' ? '工件与焊缝' : '作业详情' }) : null })
  return values
})
const rowKey = (row: WeldingRow) => 'id' in row ? row.id : 'production_date' in row ? row.production_date : `${row.device_no}:${row.robot_no}`
</script>

<template>
  <AppReportTable class="welding-table-wrap" :columns="columns" :items="items" :row-key="rowKey" :total="total" :page="page" :page-size="pageSize" :loading="loading" :paginate="paginate" @page="emit('page', $event)" @page-size="emit('page-size', $event)" />
</template>
