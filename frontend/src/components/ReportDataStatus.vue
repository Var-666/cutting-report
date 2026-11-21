<script setup lang="ts">
import type { ReportDataMeta } from '../api/generated/types.gen'
import { computed } from 'vue'
import { NButton } from 'naive-ui'

const props = defineProps<{ meta?: { report_data?: ReportDataMeta | null } | null; busy?: boolean }>()
defineEmits<{ refresh: [] }>()

const status = computed(() => props.meta?.report_data)
</script>

<template>
  <div v-if="status && (status.stale || status.refreshing || status.refresh_failed)" class="report-data-status" role="status">
    <NButton size="tiny" quaternary :type="status.stale || status.refresh_failed ? 'warning' : 'default'"
      :loading="busy" :disabled="busy" @click="$emit('refresh')">
      {{ status.refresh_failed && !status.refreshing ? '重试刷新' : status.stale ? '刷新旧数据' : '刷新' }}
    </NButton>
  </div>
</template>
