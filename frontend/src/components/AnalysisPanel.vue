<script setup lang="ts">
import { reportAsyncError } from '../api/browserObservations.ts'

import type { AnalysisRequest, AnalysisResult, LeadershipReport } from '../types/reports'
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
  workshopScope: { type: Object as PropType<AnalysisRequest['workshopScope']>, default: undefined },
  versionCheckEnabled: { type: Boolean, default: false },
  prefetchLatest: { type: Boolean, default: false },
  leadershipReport: { type: Object as PropType<LeadershipReport | null>, default: null },
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
let checkedCacheScope = ''
const deviceId = ref(getDeviceId())
const unsubscribeDevice = subscribeDevices(() => { deviceId.value = getDeviceId() })
let requestController: AbortController | null = null
let sourceCheckController: AbortController | null = null
let sourceCheckTimer: number | null = null
let resultCheckTimer: number | null = null
let refreshRequestedKey = ''
let nextAnalysisRetryAt = 0
const activationTimer = createActivationTimer({
  setTimer: (callback, delay) => window.setTimeout(callback, delay),
  clearTimer: (timer) => window.clearTimeout(timer),
})

const analysisDataVersion = computed(() => {
  if (!props.dataVersion) return ''
  return `${props.dataVersion}.${props.summaryDataVersion || props.dataVersion}`
})

function queryScope() {
  return JSON.stringify([deviceId.value, props.reportType, props.reportDate, props.periodStart, props.periodEnd, props.summaryPeriodStart, props.summaryPeriodEnd, props.versionCheckEnabled, props.workshopScope])
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
    props.workshopScope,
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
  const latestOnly = props.versionCheckEnabled && !force
  if (!activationTimer.isActive() || !deviceId.value || !props.reportDate
      || (!props.ready && !(latestOnly && props.prefetchLatest))) return
  const scope = queryScope()
  sourceCheckController?.abort()
  sourceCheckController = null
  requestController?.abort()
  requestController = new AbortController()
  const signal = requestController.signal
  const sequence = ++requestSequence
  const requestIsCurrent = () => !signal.aborted && sequence === requestSequence
  activeRequestKey = targetKey
  loading.value = true
  readingLatest.value = latestOnly
  error.value = ''
  try {
    const generate = () => analyzeReport({
      reportType: props.reportType,
      reportDate: props.reportDate,
      periodStart: props.periodStart || undefined,
      periodEnd: props.periodEnd || undefined,
      summaryPeriodStart: props.summaryPeriodStart || undefined,
      summaryPeriodEnd: props.summaryPeriodEnd || undefined,
      dataVersion: analysisDataVersion.value || undefined,
      workshopScope: props.workshopScope,
      force,
    }, { signal })
    let result = latestOnly ? await getLatestAnalysis({
      reportType: props.reportType,
      reportDate: props.reportDate,
    }, { signal }) : await generate()
    if (!requestIsCurrent() || scope !== queryScope()) return
    if (latestOnly) checkedCacheScope = scope
    if (latestOnly && !result && !analysis.value && props.ready) {
      readingLatest.value = false
      result = await generate()
    }
    if (activationTimer.isActive() && requestIsCurrent() && targetKey === requestKey()) {
      if (result?.status === 'success' && result.mode === 'model' && (!latestOnly || !analysis.value?.generated_at || !result.generated_at
          || result.generated_at >= analysis.value.generated_at)) analysis.value = result
      if (props.ready) displayedRequestKey = targetKey
      if (analysis.value) displayedScope = scope
      nextAnalysisRetryAt = 0
    }
  } catch (caught) {
    const exception = toError(caught)
    if (!signal.aborted && scope === queryScope() && exception.name !== 'AbortError' && sequence === requestSequence) {
      if (exception.status === 409) requestReportRefresh(targetKey)
      else {
        error.value = exception.message
        if (exception.status === 503) nextAnalysisRetryAt = Date.now() + (exception.retryAfterMs || 60_000)
      }
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
  if (!activationTimer.isActive() || document.visibilityState !== 'visible' || !props.ready
      || loading.value || sourceCheckController) return
  if (nextAnalysisRetryAt && Date.now() < nextAnalysisRetryAt) return
  if (!props.versionCheckEnabled && (!nextAnalysisRetryAt || Date.now() < nextAnalysisRetryAt)) return
  loadAnalysis(false).catch(reportAsyncError)
}

function scheduleAutomaticAnalysis() {
  clearScheduledAnalysis()
  if (!activationTimer.isActive()) return
  const targetKey = requestKey()
  const scope = queryScope()
  sourceCheckController?.abort()
  sourceCheckController = null
  if (targetKey !== refreshRequestedKey) refreshRequestedKey = ''
  if (scope !== checkedCacheScope) checkedCacheScope = ''
  if (scope !== displayedScope) {
    analysis.value = null
    displayedRequestKey = ''
  }
  if (!props.ready || !props.reportDate) {
    const canPrefetch = props.versionCheckEnabled && props.prefetchLatest && deviceId.value && props.reportDate
    if (canPrefetch && readingLatest.value && activeRequestKey === targetKey) return
    requestController?.abort()
    requestController = null
    requestSequence += 1
    activeRequestKey = ''
    loading.value = false
    readingLatest.value = false
    if (scope !== displayedScope) error.value = ''
    if (canPrefetch && !analysis.value && checkedCacheScope !== scope) loadAnalysis(false, targetKey).catch(reportAsyncError)
    return
  }
  if (targetKey === displayedRequestKey || (loading.value && targetKey === activeRequestKey)) return

  // The API checks the authoritative source version before it can call the model.
  requestController?.abort()
  requestController = null
  requestSequence += 1
  activeRequestKey = ''
  loading.value = false
  if (scope !== displayedScope) nextAnalysisRetryAt = 0
  loadAnalysis(false, targetKey).catch(reportAsyncError)
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
    props.workshopScope,
    props.prefetchLatest,
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
  readingLatest.value = false
}

onActivated(() => {
  activationTimer.activate()
  scheduleAutomaticAnalysis()
  if (!sourceCheckTimer) sourceCheckTimer = window.setInterval(() => { revalidateSource().catch(reportAsyncError) }, SOURCE_CHECK_INTERVAL_MS)
  if (!resultCheckTimer) resultCheckTimer = window.setInterval(pollLatestAnalysis, RESULT_CHECK_INTERVAL_MS)
})
onDeactivated(pauseAnalysis)
onBeforeUnmount(() => { pauseAnalysis(); unsubscribeDevice() })
</script>

<template>
  <ReportAnalysis :analysis="analysis" :report-version="analysisDataVersion" :leadership-report="analysisDataVersion && analysis?.data_version === analysisDataVersion ? leadershipReport : null" :loading="loading" :reading-latest="readingLatest" :error="error" :ready="ready" :empty-message="ready ? (versionCheckEnabled ? '正在等待首份成功分析，完成后将自动展示。' : '暂未生成分析') : '等待报表数据'" @reload="reloadAnalysis" @retry="retryAnalysis" />
</template>
