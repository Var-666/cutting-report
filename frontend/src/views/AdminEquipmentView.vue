<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { createReport } from '../api/reports'
import EquipmentReportView from './EquipmentReportView.vue'
import EnergyPeriodReportView from './EnergyPeriodReportView.vue'

const router = useRouter()
const exporting = ref(false)
const error = ref('')
const reportTab = ref('energy')

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
  <nav class="energy-report-tabs" aria-label="设备报表类型">
    <button type="button" :class="{ active: reportTab === 'energy' }" :aria-pressed="reportTab === 'energy'" @click="reportTab = 'energy'">能耗日 / 月报</button>
    <button type="button" :class="{ active: reportTab === 'tasks' }" :aria-pressed="reportTab === 'tasks'" @click="reportTab = 'tasks'">任务生产报表</button>
  </nav>
  <div v-if="error" class="global-error"><strong>导出任务创建失败</strong><span>{{ error }}</span><button @click="error = ''">关闭</button></div>
  <div v-if="exporting" class="route-notice">正在创建设备报表生成任务...</div>
  <KeepAlive>
    <EnergyPeriodReportView v-if="reportTab === 'energy'" />
    <EquipmentReportView v-else @export="exportReport" />
  </KeepAlive>
</template>

<style scoped>
.energy-report-tabs { display:flex; gap:8px; margin-top:22px; border-bottom:1px solid #dce5ec; padding-bottom:12px; }
.energy-report-tabs button { padding:10px 18px; border:1px solid #dce5ec; background:white; color:#506778; }
.energy-report-tabs button.active { background:#0b4f76; border-color:#0b4f76; color:white; }
</style>
