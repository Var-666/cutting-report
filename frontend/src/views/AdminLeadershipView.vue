<script setup>
import { useReportExport } from '../composables/useReportExport'
import LeadershipReportView from './LeadershipReportView.vue'

const { exporting, error, progress, exportReport } = useReportExport()
</script>

<template>
  <div v-if="error" class="global-error" role="alert"><strong>导出未完成</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <div v-if="progress" class="route-notice" role="status">{{ progress }}</div>
  <LeadershipReportView :exporting="exporting" @export="exportReport" />
</template>
