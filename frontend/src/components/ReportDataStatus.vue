<script setup lang="ts">
import type { ReportDataMeta } from '../api/generated/types.gen'
import { NButton, NTag } from 'naive-ui'
import { formatChineseDateTime } from '../utils/dateDisplay'

defineProps<{ meta?: { report_data?: ReportDataMeta | null } | null; busy?: boolean; label?: string }>()
defineEmits<{ refresh: [] }>()
</script>

<template>
  <div v-if="meta?.report_data" class="report-data-status" role="status">
    <span>{{ label || '更新于' }} <time v-if="meta.report_data.data_as_of" :datetime="meta.report_data.data_as_of">{{ formatChineseDateTime(meta.report_data.data_as_of, label ? '未知' : '--', 'Asia/Shanghai') }}</time><span v-else>{{ label ? '未知' : '--' }}</span></span>
    <NTag v-if="meta.report_data.stale" size="small" type="warning" :bordered="false">数据待更新</NTag>
    <NTag v-if="meta.report_data.refresh_failed" size="small" type="warning" :bordered="false">刷新失败，保留上次结果</NTag>
    <NTag v-else-if="meta.report_data.refreshing" size="small" type="info" :bordered="false">后台更新中</NTag>
    <NButton size="tiny" quaternary :loading="busy" :disabled="busy" @click="$emit('refresh')">刷新</NButton>
  </div>
</template>

<style scoped>
.report-data-status { display:flex; align-items:center; flex-wrap:wrap; gap:8px; min-width:0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
</style>
