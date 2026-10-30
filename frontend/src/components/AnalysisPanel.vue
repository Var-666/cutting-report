<script setup lang="ts">
import type { AnalysisRequest, AnalysisResult } from '../types/reports'
import type { PropType } from 'vue'

import { toError } from '../utils/errors'

import { computed, onActivated, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { analyzeReport, getAnalysisVersion, getLatestAnalysis } from '../api/cutting.ts'
import { createActivationTimer } from '../utils/activationTimer.ts'
import ReportAnalysis from './ReportAnalysis.vue'
import { getDeviceId, subscribeDevices } from '../api/deviceContext.ts'

const props = defineProps({
  reportType: { type: String as PropType<AnalysisRequest['reportType']>, required: true },
  reportDate: { type: String, default: '' },
  periodStart: { type: String, default: '' },
  periodEnd: { type: String, default: '' },
  summaryPeriodStart: { type: String, default: '' },
  summaryPeriodEnd: { type: String, default: '' },
  ready: { type: Boolean, default: false },
  dataVersion: { type: String, default: '' },
  summaryDataVersion: { type: String, default: '' },
  versionCheckEnabled: { type: Boolean, default: false },
})
const emit = defineEmits<{ 'refresh-report': [] }>()
const SOURCE_CHECK_INTERVAL_MS = 300_000
const RESULT_CHECK_INTERVAL_MS = 10_000

const analysis = ref<AnalysisResult | null>(null)
const loading = ref(false)
const readingLatest = ref(false)
const error = ref('')
let requestSequence = 0
let activeRequestKey = ''
let displayedRequestKey = ''
let displayedScope = ''
const deviceId = ref(getDeviceId())
const unsubscribeDevice = subscribeDevices(() => { deviceId.value = getDeviceId() })
let requestController: AbortController | null = null
let sourceCheckController: AbortController | null = null
let sourceCheckTimer: number | null = null
let resultCheckTimer: number | null = null
let refreshRequestedKey = ''
const activationTimer = createActivationTimer({
  setTimer: (callback, delay) => window.setTimeout(callback, delay),
  clearTimer: (timer) => window.clearTimeout(timer),
})

const analysisDataVersion = computed(() => {
  if (!props.dataVersion) return ''
  return `${props.dataVersion}.${props.summaryDataVersion || props.dataVersion}`
})

function queryScope() {
  return JSON.stringify([deviceId.value, props.reportType, props.reportDate, props.periodStart, props.periodEnd, props.summaryPeriodStart, props.summaryPeriodEnd, props.versionCheckEnabled])
}

function requestKey() {
  return JSON.stringify([
    deviceId.value,
    props.reportType,
    props.reportDate,
    props.periodStart,
    props.periodEnd,
    props.summaryPeriodStart,
    props.summaryPeriodEnd,
    props.versionCheckEnabled,
    analysisDataVersion.value,
  ])
}

function clearScheduledAnalysis() {
  activationTimer.cancel()
}

function requestReportRefresh(targetKey: string) {
  if (targetKey !== requestKey() || refreshRequestedKey === targetKey) return
  refreshRequestedKey = targetKey
  error.value = '报表数据已更新，正在同步后重新分析…'
  emit('refresh-report')
}

async function revalidateSource() {
  if (!activationTimer.isActive() || document.visibilityState !== 'visible'
      || !props.versionCheckEnabled || !props.ready || !props.reportDate || !analysisDataVersion.value
      || loading.value || sourceCheckController) return
  const targetKey = requestKey()
  if (!analysis.value) {
    if (refreshRequestedKey === targetKey) {
      refreshRequestedKey = ''
      requestReportRefresh(targetKey)
    }
    return
  }
  sourceCheckController = new AbortController()
  const controller = sourceCheckController
  try {
    const refreshed = await getAnalysisVersion({
      reportType: props.reportType,
      reportDate: props.reportDate,
    }, { signal: controller.signal })
    if (!controller.signal.aborted && targetKey === requestKey()
        && refreshed?.data_version
        && refreshed.data_version !== analysisDataVersion.value) requestReportRefresh(targetKey)
  } catch (caught) {
    const exception = toError(caught)
    if (!controller.signal.aborted) console.warn('智能分析版本检查失败', exception)
  } finally {
    if (sourceCheckController === controller) sourceCheckController = null
  }
}

async function loadAnalysis(force = false, targetKey = requestKey()) {
  if (!activationTimer.isActive() || !props.ready || !props.reportDate) return
  sourceCheckController?.abort()
  sourceCheckController = null
  requestController?.abort()
  requestController = new AbortController()
  const signal = requestController.signal
  const sequence = ++requestSequence
  const latestOnly = props.versionCheckEnabled && !force
  activeRequestKey = targetKey
  loading.value = true
  readingLatest.value = latestOnly
  error.value = ''
  try {
    const result = latestOnly ? await getLatestAnalysis({
      reportType: props.reportType,
      reportDate: props.reportDate,
    }, { signal }) : await analyzeReport({
      reportType: props.reportType,
      reportDate: props.reportDate,
      periodStart: props.periodStart || undefined,
      periodEnd: props.periodEnd || undefined,
      summaryPeriodStart: props.summaryPeriodStart || undefined,
      summaryPeriodEnd: props.summaryPeriodEnd || undefined,
      dataVersion: analysisDataVersion.value || undefined,
      force,
    }, { signal })
    if (activationTimer.isActive() && !signal.aborted && sequence === requestSequence) {
      if (result && (!latestOnly || !analysis.value?.generated_at || !result.generated_at
          || result.generated_at >= analysis.value.generated_at)) analysis.value = result
      displayedRequestKey = targetKey
      displayedScope = queryScope()
    }
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError' && sequence === requestSequence) {
      if (exception.status === 409) requestReportRefresh(targetKey)
      else error.value = exception.message
    }
  } finally {
    if (sequence === requestSequence) {
      loading.value = false
      readingLatest.value = false
      activeRequestKey = ''
      requestController = null
    }
  }
}

function pollLatestAnalysis() {
  if (!activationTimer.isActive() || document.visibilityState !== 'visible'
      || !props.versionCheckEnabled || loading.value || sourceCheckController) return
  void loadAnalysis(false)
}

function scheduleAutomaticAnalysis() {
  clearScheduledAnalysis()
  if (!activationTimer.isActive()) return
  const targetKey = requestKey()
  sourceCheckController?.abort()
  sourceCheckController = null
  if (targetKey !== refreshRequestedKey) refreshRequestedKey = ''
  if (!props.ready || !props.reportDate) {
    requestController?.abort()
    requestController = null
    requestSequence += 1
    activeRequestKey = ''
    loading.value = false
    // Pagination and cached-page deactivation can make ready false briefly even
    // though the report range and source version are unchanged. Preserve the
    // displayed result in that case; a changed request key still clears it.
    if (!props.reportDate || queryScope() !== displayedScope) {
      analysis.value = null
      displayedRequestKey = ''
      error.value = ''
    }
    return
  }
  if (targetKey === displayedRequestKey || (loading.value && targetKey === activeRequestKey)) return

  // The analysis API rebuilds the authoritative data_version and checks SQLite
  // before it can call the model. Abort an older response when the source changes.
  requestController?.abort()
  requestController = null
  requestSequence += 1
  activeRequestKey = ''
  loading.value = false
  if (queryScope() !== displayedScope) {
    analysis.value = null
    displayedRequestKey = ''
  }
  void loadAnalysis(false, targetKey)
}

function reloadAnalysis() {
  clearScheduledAnalysis()
  refreshRequestedKey = ''
  return loadAnalysis(true)
}

function retryAnalysis() {
  clearScheduledAnalysis()
  refreshRequestedKey = ''
  return loadAnalysis(false)
}

watch(
  () => [
    deviceId.value,
    props.reportType,
    props.reportDate,
    props.periodStart,
    props.periodEnd,
    props.summaryPeriodStart,
    props.summaryPeriodEnd,
    props.ready,
    props.dataVersion,
    props.summaryDataVersion,
    props.versionCheckEnabled,
  ],
  scheduleAutomaticAnalysis,
  { immediate: true },
)

function pauseAnalysis() {
  activationTimer.deactivate()
  clearScheduledAnalysis()
  if (sourceCheckTimer) window.clearInterval(sourceCheckTimer)
  sourceCheckTimer = null
  if (resultCheckTimer) window.clearInterval(resultCheckTimer)
  resultCheckTimer = null
  sourceCheckController?.abort()
  sourceCheckController = null
  requestController?.abort()
  requestController = null
  requestSequence += 1
  activeRequestKey = ''
  loading.value = false
}

onActivated(() => {
  activationTimer.activate()
  scheduleAutomaticAnalysis()
  if (!sourceCheckTimer) sourceCheckTimer = window.setInterval(() => { void revalidateSource() }, SOURCE_CHECK_INTERVAL_MS)
  if (!resultCheckTimer) resultCheckTimer = window.setInterval(pollLatestAnalysis, RESULT_CHECK_INTERVAL_MS)
})
onDeactivated(pauseAnalysis)
onBeforeUnmount(() => { pauseAnalysis(); unsubscribeDevice() })
</script>

<template>
  <ReportAnalysis :analysis="analysis" :loading="loading" :reading-latest="readingLatest" :error="error" :ready="ready" :empty-message="versionCheckEnabled && ready ? '后台尚无已完成的分析，生成后将自动显示。' : '报表数据加载后将自动生成分析。'" @reload="reloadAnalysis" @retry="retryAnalysis" />
</template>
