<script setup lang="ts">
import type { ReportDataMeta } from '../api/generated/types.gen'
import { computed } from 'vue'
import { NButton, NTag } from 'naive-ui'

const props = defineProps<{ meta?: { report_data?: ReportDataMeta | null } | null; busy?: boolean }>()
defineEmits<{ refresh: [] }>()

const status = computed(() => props.meta?.report_data)
</script>

<template>
  <div v-if="status && (status.stale || status.refreshing || status.refresh_failed)" class="report-data-status" role="status">
    <NTag v-if="status.refreshing && status.refresh_failed" size="small" type="warning" :bordered="false">正在重试，保留上次结果</NTag>
    <NTag v-else-if="status.refresh_failed" size="small" type="warning" :bordered="false">刷新失败，保留上次结果</NTag>
    <NTag v-else-if="status.refreshing" size="small" type="info" :bordered="false">后台更新中</NTag>
    <NTag v-else size="small" type="warning" :bordered="false">数据待更新</NTag>
    <NButton size="tiny" quaternary :loading="busy" :disabled="busy" @click="$emit('refresh')">刷新</NButton>
  </div>
</template>

<style scoped>
.report-data-status { display:flex; align-items:center; flex-wrap:wrap; gap:8px; min-width:0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
</style>
