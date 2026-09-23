<script setup>
import { useReportExport } from '../composables/useReportExport'
import ReportFeedback from '../components/ReportFeedback.vue'
import { session } from '../state/session'
import WorkshopView from './WorkshopView.vue'

const { exporting, error, progress, exportReport, retryExport } = useReportExport()
</script>

<template>
  <ReportFeedback v-if="error" type="error" title="导出未完成" :message="error" action-label="重试导出" dismissible @action="retryExport" @dismiss="error = ''" />
  <ReportFeedback v-else-if="progress" type="info" title="导出处理中" :message="progress" />
  <WorkshopView :user="session.user" mode="manager" :exporting="exporting" @export="exportReport" />
</template>
