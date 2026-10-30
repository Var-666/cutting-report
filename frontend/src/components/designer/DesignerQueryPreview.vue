<script setup lang="ts">
import type { DataTableBaseColumn } from 'naive-ui'
import type { DataRow } from '../../types/api'
import type { ColumnDefinition, QueryPreview } from '../../types/designer'
import { computed, toDisplayString } from 'vue'
import { formatChineseDate, formatChineseDateTime, formatTableDateValue } from '../../utils/dateDisplay'
import AppReportTable from '../AppReportTable.vue'

const props = defineProps<{ queryPreview: QueryPreview }>()
function formatPreviewValue(value: unknown, column: ColumnDefinition) {
  if (column?.type === 'datetime') return formatChineseDateTime(value)
  if (column?.type === 'date') return formatChineseDate(value)
  return formatTableDateValue(value)
}
const previewTableColumns = computed<DataTableBaseColumn<DataRow>[]>(() => (props.queryPreview?.columns || []).map((column, index) => ({
  key: `c${index}`,
  title: column.label,
  ...(['number', 'duration'].includes(column.type)
    ? { width: 128, align: 'right' as const }
    : column.type === 'date' || column.type === 'datetime'
      ? { width: column.type === 'date' ? 152 : 184, align: 'left' as const }
      : { minWidth: 160, align: 'left' as const }),
  render: row => toDisplayString(formatPreviewValue(row[column.field], column)),
})))
</script>

<template>
  <div class="designer-preview-table"><div>预览返回 {{ queryPreview.returned }} 行 · {{ queryPreview.mode }}</div><div class="history-wrap">
          <AppReportTable :columns="previewTableColumns" :items="queryPreview.items" :row-key="row => queryPreview?.items.indexOf(row) ?? -1" :paginate="false" aria-label="查询数据预览" />
        </div></div>
</template>
