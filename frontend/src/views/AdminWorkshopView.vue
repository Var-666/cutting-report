<script setup>
import { useReportExport } from '../composables/useReportExport'
import { session } from '../state/session'
import WorkshopView from './WorkshopView.vue'

const { exporting, error, progress, exportReport } = useReportExport()
</script>

<template>
  <div v-if="error" class="global-error" role="alert"><strong>导出未完成</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <div v-if="progress" class="route-notice" role="status">{{ progress }}</div>
  <WorkshopView :user="session.user" mode="manager" :exporting="exporting" @export="exportReport" />
</template>
