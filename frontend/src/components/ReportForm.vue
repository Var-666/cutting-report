<script setup>
import { computed, reactive, watch } from 'vue'

const props = defineProps({
  loading: Boolean,
  projects: { type: Array, default: () => [] },
  ships: { type: Array, default: () => [] },
  customDefinitions: { type: Array, default: () => [] },
  initialReportType: { type: String, default: 'leadership' },
  lockedReportType: Boolean,
})

const selectedDefinition = computed(() => {
  if (!form.report_type.startsWith('custom:')) return null
  const definitionId = form.report_type.slice(7)
  return props.customDefinitions.find((item) => item.definition_id === definitionId) || null
})
const isMonthly = computed(() => form.report_type === 'leadership' || selectedDefinition.value?.period_type === 'month')
const isLeadership = computed(() => form.report_type === 'leadership')
const emit = defineEmits(['submit', 'date-change', 'project-change'])

const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
const form = reactive({
  report_type: props.initialReportType,
  report_date: today,
  report_month: today.slice(0, 7),
  chart_type: 'bar_line',
  project_code: '',
  ship_no: '',
})

watch(
  () => props.initialReportType,
  (value) => {
    if (value) form.report_type = value
  },
)

watch(
  () => form.report_date,
  (value) => emit('date-change', value),
  { immediate: true },
)
watch(
  () => form.project_code,
  (value) => {
    form.ship_no = ''
    emit('project-change', { reportDate: form.report_date, projectCode: value })
  },
)

function submit() {
  const definition = selectedDefinition.value
  emit('submit', {
    report_type: definition ? 'custom' : form.report_type,
    definition_id: definition?.definition_id || null,
    report_date: isMonthly.value ? `${form.report_month}-01` : form.report_date,
    project_code: isLeadership.value ? null : form.project_code || null,
    ship_no: isLeadership.value ? null : form.ship_no || null,
    parameters: definition ? {} : { chart_type: form.chart_type },
  })
}

function reset() {
  form.report_date = today
  form.report_month = today.slice(0, 7)
  form.report_type = props.initialReportType
  form.chart_type = 'bar_line'
  form.project_code = ''
  form.ship_no = ''
}
</script>

<template>
  <form class="report-form" @submit.prevent="submit">
    <label>
      <span>报告类型 <b>*</b></span>
      <select v-model="form.report_type" :disabled="loading || lockedReportType">
        <option value="leadership">领导报表</option>
        <option value="workshop">车间报表</option>
        <option value="equipment">设备报表</option>
        <optgroup v-if="customDefinitions.length" label="自定义报表">
          <option v-for="item in customDefinitions" :key="item.definition_id" :value="`custom:${item.definition_id}`">{{ item.name }}</option>
        </optgroup>
      </select>
    </label>
    <label v-if="!selectedDefinition">
      <span>图表类型</span>
      <select v-model="form.chart_type" :disabled="loading">
        <option value="bar_line">柱线组合</option>
        <option value="bar">柱状图</option>
        <option value="line">折线图</option>
        <option value="area">面积图</option>
      </select>
    </label>
    <label>
      <span>{{ isMonthly ? '报告月份' : '报告日期' }} <b>*</b></span>
      <input v-if="isMonthly" v-model="form.report_month" type="month" required :disabled="loading" />
      <input v-else v-model="form.report_date" type="date" required :disabled="loading" />
    </label>
    <label v-if="!isLeadership">
      <span>工程号</span>
      <select v-model="form.project_code" :disabled="loading">
        <option value="">全部工程</option>
        <option v-for="item in projects" :key="item" :value="item">{{ item }}</option>
      </select>
    </label>
    <label v-if="!isLeadership">
      <span>船号</span>
      <select v-model="form.ship_no" :disabled="loading">
        <option value="">全部船号</option>
        <option v-for="item in ships" :key="item" :value="item">{{ item }}</option>
      </select>
    </label>
    <div class="form-actions">
      <button class="primary" type="submit" :disabled="loading || (isMonthly ? !form.report_month : !form.report_date)">
        <span v-if="loading" class="spinner"></span>
        {{ loading ? '生成中' : '生成报告' }}
      </button>
      <button type="button" :disabled="loading" @click="reset">重置</button>
    </div>
  </form>
</template>
