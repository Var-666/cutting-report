<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReport } from '../api/reports'
import LeadershipReportView from './LeadershipReportView.vue'

const router = useRouter()
const exporting = ref(false)
const error = ref('')

async function exportReport(payload) {
  exporting.value = true
  error.value = ''
  try {
    const job = await createReport(payload)
    await router.push({ name: 'report-center', query: { job: job.report_id } })
  } catch (exception) {
    error.value = exception.message
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div v-if="error" class="global-error"><strong>导出任务创建失败</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <div v-if="exporting" class="route-notice">正在创建领导报表生成任务...</div>
  <LeadershipReportView @export="exportReport" />
</template>
