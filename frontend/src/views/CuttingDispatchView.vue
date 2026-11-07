<script setup lang="ts">
import AppReportTable from '../components/AppReportTable.vue'
import DispatchBatchDialog from '../components/dispatch/DispatchBatchDialog.vue'
import DispatchHistoryTable from '../components/dispatch/DispatchHistoryTable.vue'
import DispatchHistoryTrend from '../components/dispatch/DispatchHistoryTrend.vue'
import { useCuttingFileBrowser } from '../composables/useCuttingFileBrowser'
import { useCuttingDispatchHistory } from '../composables/useCuttingDispatchHistory'
import { useCuttingDispatchBatch } from '../composables/useCuttingDispatchBatch'
import type { ReadOptions } from '../types/api'
import type { CuttingDispatch, CuttingFile, CuttingMachine, CuttingStatus } from '../types/cutting'

import { toError } from '../utils/errors'
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAutoComplete, NTag } from 'naive-ui'
import CatalogTreeNode from '../components/CatalogTreeNode.vue'
import AppIcon from '../components/AppIcon.vue'
import { parseCuttingInstructionFilename } from '../utils/cuttingInstructionName.ts'
import { cuttingFileKey as fileKey } from '../utils/cuttingDispatchBatch.ts'
import { formatChineseDateTime } from '../utils/dateDisplay.ts'
import { useConfirm } from '../composables/useConfirm.ts'
import { executeCuttingDispatch, getCuttingStatus, listCuttingMachines } from '../api/dispatch.ts'

const confirm = useConfirm()
const pageActive = ref(true)
const route = useRoute()
const router = useRouter()
let lifecycleSequence = 0
let lifecycleController: AbortController | null = null
let statusController: AbortController | null = null
let firstActivation = true
const STATUS_CHECK_TIMEOUT_MS = 30_000

function beginLifecycleRequest() {
  lifecycleController?.abort()
  lifecycleController = new AbortController()
  return { sequence: ++lifecycleSequence, signal: lifecycleController.signal }
}
const dispatchForm = reactive({ machineIp: '' })
const systemStatus = ref<CuttingStatus | null>(null)
const statusLoading = ref(false)
const statusError = ref('')
const machines = ref<CuttingMachine[]>([])
const operatingId = ref('')
const retryDispatch = ref<CuttingDispatch | null>(null)
const retrySegment = ref('')
const notice = ref('')
const noticeType = ref('info')
const initialProjectId = Array.isArray(route.query.project_id)
  ? route.query.project_id[0] || ''
  : String(route.query.project_id || '')
const {
  rows, selectedFiles, selectingAll, loading, page,
  requestedPage, pageSize, total, projectOptions, projectSearch,
  projectSuggestions, selectedProjectId, projects, catalogTree, fileKeyword,
  appliedFileKeyword, fileError, selectedProject, selectedCatalog, projectExpanded,
  expandedCatalogIds, projectLoading, catalogLoading, totalPages, allCatalogFilesSelected,
  someCatalogFilesSelected, selectedPath, clearSelectedFiles, setFileSelected, toggleCatalogFiles,
  expandCatalog, loadProjects, openSelectedProject, selectProject, toggleProject,
  selectCatalog, applyFileSearch, loadFiles, changePage, refreshVisibleFileStatuses,
  projectsLoaded, pauseDirectory,
} = useCuttingFileBrowser({
  pageActive,
  systemStatus,
  isDispatching: () => pushBatch.saving,
  initialProjectId,
  rememberSelectedProject,
  notice,
  showNotice,
})
const {
  histories, historyPage, historyPageSize, historyTotal, historyLoading,
  historyError, loadHistory, changeHistoryPage, startHistoryPolling, pauseHistory,
  trend, trendLoading, trendError, trendFilters, selectedHistoryDay, historyDateType, historySelectionLabel,
  loadTrend, refreshHistory, applyHistoryFilters, selectHistoryDay, clearHistoryFilters,
} = useCuttingDispatchHistory({
  pageActive,
  isBusy: () => Boolean(operatingId.value || pushBatch.saving),
})
const {
  bulkQuantity, pushBatch, openPushBatch, closePushBatch, removePushBatchItem, applyBulkQuantity,
  invalidateBatchConfirmation, submitPushBatch, retryBatchItem, checkBatchItem, refreshBatchCatalog,
} = useCuttingDispatchBatch({
  selectedFiles,
  clearSelectedFiles,
  dispatchForm,
  systemStatus,
  operatingId,
  loadHistory: refreshHistory,
  refreshVisibleFileStatuses,
  loadFiles,
  showNotice,
})

const selectedFileCount = computed(() => selectedFiles.value.size)
const historyMachineIps = computed(() => [...new Set([
  ...(trend.value?.machine_ips || []), ...machines.value.map(machine => machine.machine_ip),
  ...(systemStatus.value?.magic_box?.machine_ips || []),
  ...(trendFilters.value.machine_ip ? [trendFilters.value.machine_ip] : []),
])].sort())

function machineName(machineIp: string) {
  const names: Record<string, string> = {
    '10.147.1.40': '20kW 切割机',
    '10.147.1.38': '30kW 切割机',
  }
  return names[machineIp] || machines.value.find((machine) => machine.machine_ip === machineIp)?.server_name || '未命名机床'
}

function showNotice(message: string, type = 'info') {
  notice.value = message
  noticeType.value = type
}

function rememberSelectedProject(projectId: string) {
  const value = String(projectId || '')
  const current = Array.isArray(route.query.project_id)
    ? String(route.query.project_id[0] || '')
    : String(route.query.project_id || '')
  if (value === current) return
  const query = { ...route.query }
  if (value) query.project_id = value
  else delete query.project_id
  router.replace({ query })
}

function openSinglePush(file: CuttingFile) {
  if (pushBatch.saving) return
  selectedFiles.value = new Map([[fileKey(file), file]])
  openPushBatch()
}

async function loadMachines({ signal }: ReadOptions = {}) {
  if (!systemStatus.value?.magic_box?.configured) return
  try {
    const data = await listCuttingMachines({ signal })
    if (signal?.aborted || !pageActive.value) return
    machines.value = data.items || []
    if (!dispatchForm.machineIp && machines.value.length) {
      dispatchForm.machineIp = machines.value[0].machine_ip
    }
  } catch (caught) {
    const error = toError(caught)
    if (signal?.aborted || !pageActive.value || error.name === 'AbortError') return
    machines.value = (systemStatus.value?.magic_box?.machine_ips || []).map((machineIp) => ({
      machine_ip: machineIp,
      online: false,
      configured: true,
    }))
    if (!dispatchForm.machineIp && machines.value.length) dispatchForm.machineIp = machines.value[0].machine_ip
    showNotice(`机床在线状态读取失败：${error.message}。仍可选择 SYS_CONFIG 中配置的机床。`, 'error')
  }
}

async function executeSaved(item: CuttingDispatch, segmentName?: string) {
  if (operatingId.value) return
  if (!item.remote_task_id && !parseCuttingInstructionFilename(item.document_no) && segmentName === undefined) {
    retryDispatch.value = item
    retrySegment.value = ''
    return
  }
  if (!await confirm(`确认执行任务 ${item.dispatch_id}？\n文件：${item.document_no}\n机床：${machineName(item.machine_ip)}`)) return
  retryDispatch.value = null
  operatingId.value = item.dispatch_id
  try {
    const completed = await executeCuttingDispatch(item.dispatch_id, segmentName)
    if (completed.local_file_cleanup?.error) {
      showNotice(`推送完成，但本地文件清理失败：${completed.local_file_cleanup.error}`, 'error')
    } else {
      showNotice(`推送完成，已删除系统中的文件副本。魔盒任务 UUID：${completed.remote_task_id}`)
    }
  } catch (caught) {
    const error = toError(caught)
    showNotice(error.message, 'error')
  } finally {
    operatingId.value = ''
    await Promise.allSettled([refreshHistory(), refreshVisibleFileStatuses()])
  }
}

function formatTime(value: unknown) { return formatChineseDateTime(value) }

function fileExtension(row: CuttingFile) {
  const source = String(row.document_no || row.physical_file_name || row.normalized_path || '')
  const filename = source.split(/[\\/]/).pop() || ''
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot).toUpperCase() : '--'
}

async function loadRuntimeStatus({ signal, force = false }: ReadOptions = {}) {
  if (!pageActive.value || signal?.aborted) return
  statusController?.abort()
  const controller = new AbortController()
  statusController = controller
  const sequence = lifecycleSequence
  const cancel = () => controller.abort()
  signal?.addEventListener('abort', cancel, { once: true })
  let timedOut = false
  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, STATUS_CHECK_TIMEOUT_MS)
  statusLoading.value = true
  statusError.value = ''
  try {
    const data = await getCuttingStatus('push', { signal: controller.signal, force })
    if (controller.signal.aborted || !pageActive.value || sequence !== lifecycleSequence
        || statusController !== controller) return
    systemStatus.value = data
    window.clearTimeout(timer)
    statusLoading.value = false
    await loadMachines({ signal: controller.signal })
  } catch (caught) {
    const error = toError(caught)
    if (!pageActive.value || signal?.aborted || sequence !== lifecycleSequence
        || statusController !== controller) return
    if (timedOut || error.name !== 'AbortError') {
      systemStatus.value = null
      statusError.value = timedOut
        ? '文件派发状态检查超时（30秒），项目目录可继续浏览，请重新检查后再派发。'
        : `文件派发状态检查失败：${error.message}`
    }
  } finally {
    window.clearTimeout(timer)
    signal?.removeEventListener('abort', cancel)
    if (statusController === controller) {
      statusLoading.value = false
      statusController = null
    }
  }
}

async function initializePage({ refreshProjects = true } = {}) {
  const { sequence, signal } = beginLifecycleRequest()
  try {
    await Promise.allSettled([
      loadRuntimeStatus({ signal }),
      ...(refreshProjects ? [loadProjects()] : []),
      refreshHistory({ signal }),
    ])
  } catch (caught) {
    const error = toError(caught)
    if (error.name !== 'AbortError' && sequence === lifecycleSequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === lifecycleSequence) {
      lifecycleController = null
    }
  }
}

function pausePage() {
  if (!pageActive.value) return
  pageActive.value = false
  retryDispatch.value = null
  lifecycleSequence += 1
  lifecycleController?.abort()
  lifecycleController = null
  statusController?.abort()
  statusController = null
  statusLoading.value = false
  pauseDirectory()
  pauseHistory()
}

onMounted(() => {
  startHistoryPolling()
  initializePage()
})

onActivated(async () => {
  pageActive.value = true
  startHistoryPolling()
  if (firstActivation) {
    firstActivation = false
    return
  }
  await initializePage({ refreshProjects: !projectsLoaded.value })
})
onDeactivated(pausePage)
onBeforeUnmount(pausePage)

const fileTableColumns = [
  { key: 'c0', title: '', width: 48, align: 'center', fixed: 'left', ellipsis: false },
  { key: 'c1', title: "文件名", minWidth: 220, align: 'left' },
  { key: 'c2', title: "命名检查", width: 104, align: 'center' },
  { key: 'c3', title: "中间产品", width: 112, align: 'left' },
  { key: 'c4', title: "文件格式", width: 80, align: 'left' },
  { key: 'c5', title: "版本", width: 72, align: 'left' },
  { key: 'c6', title: "操作人", width: 96, align: 'left' },
  { key: 'c7', title: "操作人工号", width: 112, align: 'left' },
  { key: 'c8', title: "操作时间", width: 184, align: 'left' },
  { key: 'c9', title: "派发状态", width: 184, align: 'center' },
  { key: 'c10', title: "操作", width: 148, align: 'center', fixed: 'right', ellipsis: false }
] as const

</script>

<template>
  <header class="dispatch-heading">
    <h1>文件派发</h1>
    <div class="dispatch-source-status" aria-label="文件派发服务状态">
      <span><i :class="{ ready: systemStatus?.can_push }"></i>{{ systemStatus?.pdm?.label || (statusError ? '数据源检查失败' : '正在检查数据源') }}</span>
      <span>{{ systemStatus?.file_source?.label || (statusError ? '文件服务状态未确认' : '文件服务检查中') }}</span>
      <span>{{ systemStatus?.safe_mode ? '安全联调模式' : '生产数据模式' }}</span>
    </div>
  </header>

  <div v-if="statusError" class="dispatch-notice error" role="alert">
    <span>{{ statusError }}</span>
    <NButton attr-type="button" :disabled="statusLoading" @click="loadRuntimeStatus({ force: true })">重新检查</NButton>
  </div>
  <div v-if="notice" class="dispatch-notice" :class="{ error: noticeType === 'error' }" :role="noticeType === 'error' ? 'alert' : 'status'">
    <span>{{ notice }}</span>
    <NButton attr-type="button" @click="notice = ''">关闭</NButton>
  </div>

  <div class="dispatch-workspace download-workspace">
    <aside class="panel directory-panel">
      <div class="panel-title">
        <div><span class="dispatch-section-icon"><AppIcon name="briefcase" size="17" /></span><h2>项目目录</h2></div>
        <p>{{ projectOptions.length }} 个支持项目</p>
      </div>
      <div class="project-search">
        <label for="project-selector">支持项目</label>
        <!-- Suggestions omit label so selecting a project keeps the search text editable. -->
        <NAutoComplete v-model:value="projectSearch" class="project-selector" :input-props="{ id: 'project-selector', 'aria-label': '搜索并选择支持项目', name: 'cutting_project' }" :options="projectSuggestions" :get-show="() => true" :render-label="option => (option as { displayLabel: string }).displayLabel" :menu-props="{ class: 'dispatch-project-menu', style: { width: 'max-content', maxWidth: 'calc(100vw - 24px)' } }" clearable show-empty :disabled="projectLoading || !projectOptions.length" :placeholder="projectLoading ? '正在读取项目…' : projectOptions.length ? '输入项目名称或船号…' : '暂无支持项目'" @select="openSelectedProject">
          <template #empty>没有匹配的支持项目</template>
        </NAutoComplete>
      </div>
      <div class="directory-scroll" :aria-busy="projectLoading || catalogLoading">
        <div v-if="projectLoading" class="directory-message">正在读取项目…</div>
        <ul v-else-if="projects.length" class="project-tree" role="tree" aria-label="PDM项目目录">
          <li v-for="project in projects" :key="project.project_id" class="project-node" role="treeitem" :aria-expanded="selectedProject?.project_id === project.project_id && projectExpanded">
            <div class="project-node-row" :class="{ selected: selectedProject?.project_id === project.project_id && !selectedCatalog }">
              <button class="project-toggle" type="button" :aria-label="`${selectedProject?.project_id === project.project_id && projectExpanded ? '收起' : '展开'}${project.project_name}`" @click="toggleProject(project)"><span :class="{ expanded: selectedProject?.project_id === project.project_id && projectExpanded }">›</span></button>
              <span class="project-folder" aria-hidden="true"></span>
              <button class="project-label" type="button" :title="project.project_name" @click="selectProject(project)"><strong>{{ project.project_name }}</strong><small>{{ project.ship_no || '未标注船号' }}</small></button>
            </div>
            <ul v-if="selectedProject?.project_id === project.project_id && projectExpanded" class="project-catalogs" role="group">
              <li v-if="catalogLoading" class="directory-message">正在读取目录…</li>
              <template v-else><CatalogTreeNode v-for="node in catalogTree" :key="node.catalog_id" :node="node" :selected-id="String(selectedCatalog?.catalog_id || '')" :expanded-ids="expandedCatalogIds" @select="selectCatalog" @toggle="expandCatalog" /></template>
              <li v-if="!catalogLoading && !catalogTree.length" class="directory-message">该项目没有目录</li>
            </ul>
          </li>
        </ul>
        <div v-else class="directory-message">没有找到匹配的项目</div>
      </div>
    </aside>

    <section class="panel dispatch-files-panel">
      <div class="panel-title">
        <div><span class="dispatch-section-icon blue"><AppIcon name="file" size="17" /></span><h2>目录文件</h2></div>
        <p>{{ selectedCatalog ? `共 ${total} 条 · 当前第 ${page} 页` : '请选择左侧目录' }}</p>
      </div>
      <div class="selected-directory"><span>当前位置</span><strong>{{ selectedPath }}</strong></div>
      <form class="file-name-search" @submit.prevent="applyFileSearch">
        <label for="file-keyword">当前目录内查找文件</label>
        <div><NInput id="file-keyword" v-model:value.trim="fileKeyword" name="file_keyword" autocomplete="off" :disabled="!selectedCatalog" placeholder="输入文件名或中间产品…" :input-props="{ 'id': 'file-keyword', 'name': 'file_keyword', 'autocomplete': 'off' }" /><NButton attr-type="submit" :disabled="!selectedCatalog || loading">{{ loading ? '查询中…' : '查询' }}</NButton></div>
      </form>
      <div v-if="fileError" class="dispatch-notice error" role="alert"><span>文件读取失败：{{ fileError }}{{ rows.length ? '；当前仍显示上次查询结果。' : '' }}</span><NButton :disabled="loading" @click="applyFileSearch">重试</NButton></div>
      <div class="download-batch-toolbar" aria-live="polite">
        <span>已选择 <strong>{{ selectedFileCount }}</strong> / 100 项<small>全选包含所有分页</small></span>
        <div><NButton attr-type="button" :disabled="!selectedFileCount || pushBatch.saving" @click="clearSelectedFiles">清空选择</NButton><NButton class="primary" attr-type="button" aria-label="批量设置参数并派发已选文件" :disabled="!selectedFileCount || loading || pushBatch.saving || !systemStatus?.can_push" @click="openPushBatch" type="primary">设置参数并派发</NButton></div>
      </div>
      <div class="dispatch-table-wrap" :aria-busy="loading">
        <div v-if="loading" class="dispatch-loading" role="status">{{ selectingAll ? '正在选择所有分页的文件…' : `正在加载第 ${requestedPage} 页…` }}</div>
        <AppReportTable :flex-height="rows.length === 0" :max-height="null" :columns="fileTableColumns" :items="rows" :total="total" :page="page" :page-size="pageSize" :page-sizes="[]" @page="changePage" :row-key="row => fileKey(row)" aria-label="文件查询结果" :row-class-name="row => (selectedFiles.has(fileKey(row))) ? 'selected' : ''" :loading="loading">
        <template #header-c0><div class="download-check-cell"><NCheckbox :checked="allCatalogFilesSelected" :indeterminate="someCatalogFilesSelected" :disabled="!total || !systemStatus?.can_push || loading || pushBatch.saving" aria-label="全选当前目录所有分页的可派发文件" @update:checked="toggleCatalogFiles($event)" /></div></template>
        <template #c0="{ row }"><div class="download-check-cell"><NCheckbox :checked="selectedFiles.has(fileKey(row))" :disabled="loading || pushBatch.saving || !row.path_available || !systemStatus?.can_push" :aria-label="`选择文件 ${row.document_no}`" @update:checked="setFileSelected(row, $event)" /></div></template>
        <template #c1="{ row }"><div class="file-name-cell"><strong>{{ row.document_no }}</strong></div></template>
        <template #c2="{ row }"><span class="filename-standard" :class="{ warning: !parseCuttingInstructionFilename(row.document_no) }">{{ parseCuttingInstructionFilename(row.document_no) ? '标准名' : '非标准名' }}</span></template>
        <template #c3="{ row }">{{ row.pbs_info || '--' }}</template>
        <template #c4="{ row }"><div class="file-extension">{{ fileExtension(row) }}</div></template>
        <template #c5="{ row }">{{ row.version || row.physical_version_code?.trim() || row.physical_version_no || '--' }}</template>
        <template #c6="{ row }">{{ row.operator || row.physical_file_operator || '--' }}</template>
        <template #c7="{ row }">{{ row.operator_job_no || row.physical_file_operator_job_no || '--' }}</template>
        <template #c8="{ row }">{{ formatTime(row.operate_time || row.physical_file_date) }}</template>
        <template #c9="{ row }"><template v-if="row.dispatch_status"><NTag class="dispatch-status" :class="row.dispatch_status.status" size="small" :type="row.dispatch_status.status === 'failed' ? 'error' : 'info'">{{ row.dispatch_status.status_label }}</NTag><small class="dispatch-project-name">{{ row.dispatch_status.stage_label }}</small></template><span v-else class="dispatch-project-name">暂无记录</span></template>
        <template #c10="{ row }"><div class="file-download-cell"><NButton attr-type="button" :disabled="loading || pushBatch.saving || !row.path_available || !systemStatus?.can_push" :aria-label="`派发 ${row.document_no}`" @click="openSinglePush(row)">设置并派发</NButton><small v-if="!row.path_available" :title="row.path_error || '路径不可用'">{{ row.path_error || '路径不可用' }}</small></div></template>
      <template #empty>{{ fileError ? '文件读取失败，请重试' : selectedCatalog ? appliedFileKeyword ? '当前目录没有匹配的文件' : '当前目录没有文件' : '请从左侧选择一个目录' }}</template>
      </AppReportTable>
      </div>
      <p v-if="systemStatus && !systemStatus.can_push" class="dispatch-download-hint">当前文件来源或魔盒服务不可用，暂不可派发。</p>
    </section>

  </div>

  <section class="panel dispatch-history-panel" :aria-busy="historyLoading">
    <div class="panel-title"><div><span class="dispatch-section-icon green"><AppIcon name="check" size="17" /></span><h2>派发记录</h2></div></div>
    <DispatchHistoryTrend :trend="trend" :filters="trendFilters" :loading="trendLoading" :error="trendError" :selected-day="selectedHistoryDay" :selected-date-type="historyDateType" :machine-ips="historyMachineIps" :machine-name="machineName" :disabled="!!operatingId || pushBatch.saving" @apply="applyHistoryFilters" @select-day="selectHistoryDay" @clear="clearHistoryFilters" @retry="loadTrend()" />
    <p class="dispatch-history-scope">当前明细：{{ historySelectionLabel }}<template v-if="trendFilters.machine_ip"> · {{ machineName(trendFilters.machine_ip) }}（{{ trendFilters.machine_ip }}）</template></p>
    <div v-if="historyError" class="global-error" role="alert"><span>派发记录读取失败：{{ historyError }}</span><NButton attr-type="button" :disabled="historyLoading" @click="loadHistory()">重试</NButton></div>
    <div v-if="historyLoading && !histories.length" class="dispatch-empty-history" role="status"><span class="spinner dark" aria-hidden="true"></span><strong>正在读取派发记录</strong></div>
    <div v-else-if="histories.length" class="dispatch-table-wrap"><DispatchHistoryTable :histories="histories" :history-total="historyTotal" :history-page="historyPage" :history-page-size="historyPageSize" :project-options="projectOptions" :machine-name="machineName" :timezone="trend?.timezone" :loading="historyLoading || !!operatingId || pushBatch.saving" @page="changeHistoryPage" /></div>
    <div v-else-if="!historyError" class="dispatch-empty-history"><i><AppIcon name="send" size="20" /></i><strong>暂无派发记录</strong></div>

  </section>

  <div v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar-spacer" aria-hidden="true"></div>
  <aside v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar" aria-label="已选文件操作">
    <span><small>已选</small><strong>{{ selectedFileCount }}</strong><small>/ 100 项</small></span>
    <NButton attr-type="button" :disabled="pushBatch.saving" @click="clearSelectedFiles">清空</NButton>
    <NButton class="primary" attr-type="button" aria-label="批量设置参数并派发已选文件" :disabled="loading || pushBatch.saving || !systemStatus?.can_push" @click="openPushBatch" type="primary"><AppIcon name="send" size="16" tone="light" />设置参数并派发</NButton>
  </aside>

    <NModal :show="!!retryDispatch" @update:show="visible => { if (!visible) retryDispatch = null }">
      <div class="retry-dispatch-dialog panel" role="dialog" aria-modal="true" aria-labelledby="retry-dispatch-title"><form @submit.prevent="retryDispatch && executeSaved(retryDispatch, retrySegment.trim())">
        <h2 id="retry-dispatch-title">补充分段名</h2>
        <p>{{ retryDispatch?.document_no }} 为非标准文件名，请填写分段名后核对执行。</p>
        <label><span>分段名</span><NInput v-model:value.trim="retrySegment" :maxlength="80" :input-props="{ name: 'retry_segment', required: true }" placeholder="例如：806C" /></label>
        <footer><NButton @click="retryDispatch = null">取消</NButton><NButton type="primary" attr-type="submit" :disabled="!retrySegment.trim()">核对并继续</NButton></footer>
      </form></div>
    </NModal>
    <DispatchBatchDialog
      :push-batch="pushBatch" v-model:machine-ip="dispatchForm.machineIp" v-model:bulk-quantity="bulkQuantity"
      :machines="machines" :machine-name="machineName" :can-push="!!systemStatus?.can_push"
      @close="closePushBatch" @submit="submitPushBatch" @apply-quantity="applyBulkQuantity"
      @parameters-changed="invalidateBatchConfirmation" @remove="removePushBatchItem"
      @retry="retryBatchItem" @check="checkBatchItem" @refresh-catalog="refreshBatchCatalog"
    />
</template>

<style scoped>
.dispatch-heading { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px 16px; min-height:40px; }
.dispatch-heading h1 { margin:0; font-size:20px; font-weight:600; }

.retry-dispatch-dialog{width:min(480px,calc(100vw - 32px));padding:24px}.retry-dispatch-dialog footer{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}

.dispatch-project-name { color:var(--muted); font-size:var(--font-auxiliary); white-space:nowrap; }
.dispatch-notice { display:flex; align-items:center; gap:12px; margin-bottom:22px; padding:13px 16px; color:var(--text-color-1); background:rgb(var(--info-color) / .1); font-size:12px; margin:10px 0 0; border:1px solid var(--theme-border-color); border-left:3px solid var(--theme-border-color); border-radius:8px; }
.dispatch-notice.error { color:rgb(var(--error-color)); background:rgb(var(--error-color) / .1); border-color:rgb(var(--error-color)); border-left-color:rgb(var(--error-color)); }
.dispatch-notice button { min-height:30px; margin-left:auto; padding:0 8px; }
.dispatch-workspace { display:grid; grid-template-columns:minmax(0,1.8fr) minmax(300px,.72fr); gap:24px; align-items:start; margin:24px 0 0; }
.dispatch-workspace.push-workspace { grid-template-columns:1fr; align-items:stretch; }
.dispatch-files-panel { min-width:0; }
.download-workspace .dispatch-files-panel { display:flex; min-height:600px; flex-direction:column; }
.download-workspace .dispatch-table-wrap { display:flex; flex:1 1 auto; min-height:260px; }
.directory-panel { min-width:0; }
.project-search,.file-name-search { padding:15px 18px; border-bottom:1px solid var(--theme-border-color); }
.project-search label,.file-name-search label { display:block; margin-bottom:6px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.project-search label small { margin-left:5px; color:var(--text-color-3); font-size:var(--font-auxiliary); font-weight:400; }
.project-search > div,.file-name-search > div { display:flex; gap:7px; }
.project-search .project-selector,.file-name-search input { width:100%; min-width:0; }
.project-search .project-selector { height:36px; font-size:12px; }
.project-search :deep(.n-input) { height:36px; font-size:12px; }
:global(.dispatch-project-menu .n-base-select-option__content) { min-width:0; white-space:normal!important; overflow-wrap:anywhere; }
.project-search button,.file-name-search button { min-width:58px; padding:0 11px; }
.project-search button { min-height:36px; height:36px; }
.directory-scroll { padding:10px 9px 15px; }
.project-tree,.project-catalogs { margin:0; padding:0; list-style:none; }
.project-catalogs { margin-left:18px; border-left:1px solid var(--theme-border-color); }
.project-node-row { display:flex; align-items:center; min-height:40px; padding:3px 6px 3px 2px; }
.project-toggle { width:25px; height:30px; display:grid; place-items:center; flex:0 0 25px; padding:0; border:0; color:var(--text-color-2); background:transparent; }
.project-toggle span { display:block; font-size:21px; line-height:1; transition:transform .15s ease; }
.project-toggle span.expanded { transform:rotate(90deg); }
.project-folder { position:relative; width:19px; height:13px; flex:0 0 19px; margin:0 8px 0 1px; border:1px solid var(--theme-border-color); border-radius:2px; }
.project-folder::before { content:''; position:absolute; left:1px; top:-5px; width:9px; height:4px; border:1px solid var(--theme-border-color); border-bottom:0; border-radius:2px 2px 0 0; }
.project-label { min-width:0; display:flex; align-items:flex-start; flex-direction:column; padding:6px 2px; border:0; background:transparent; text-align:left; cursor:pointer; }
.project-label strong { max-width:100%; color:var(--text-color-1); overflow-wrap:anywhere; white-space:normal; font-size:12px; }
.project-label small { margin-top:2px; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.directory-message { padding:18px 12px; color:var(--muted); text-align:center; font-size:var(--font-auxiliary); }
.selected-directory { display:flex; align-items:center; gap:10px; border-bottom:1px solid rgb(var(--primary-color)); }
.selected-directory span { flex:0 0 auto; font-size:var(--font-auxiliary); font-weight:700; letter-spacing:1px; }
.selected-directory strong { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.file-name-search { display:flex; align-items:end; justify-content:space-between; gap:18px; }
.file-name-search > div { width:min(430px,100%); }
.file-name-search label { margin:0; flex:0 0 auto; }
.download-batch-toolbar { display:flex; align-items:center; justify-content:space-between; gap:14px; border-bottom:1px solid var(--theme-border-color); }
.download-batch-toolbar > span { color:var(--muted); font-size:var(--font-auxiliary); }
.download-batch-toolbar > span strong { margin:0 3px; color:var(--blue); font-size:15px; font-variant-numeric:tabular-nums; }
.download-batch-toolbar > span small { margin-left:10px; color:rgb(var(--warning-color)); font-size:inherit; }
.download-batch-toolbar > div { display:flex; gap:7px; }
.download-batch-toolbar button { padding:0 12px; white-space:nowrap; }
.download-batch-toolbar button:disabled { cursor:not-allowed; }
.dispatch-table-wrap { overflow-x:auto; position:relative; min-height:75px; }
.dispatch-history-panel > .dispatch-table-wrap { min-width:0; padding:16px; }
.dispatch-loading { position:absolute; z-index:2; inset:0; display:flex; align-items:flex-start; justify-content:center; padding-top:48px; background:rgb(var(--info-color) / .1); color:var(--blue); font-size:12px; }.file-download-cell button { white-space:nowrap; cursor:pointer; }.file-download-cell button:disabled { cursor:not-allowed; }.file-download-cell small { min-width:0; overflow:hidden; text-overflow:ellipsis; color:var(--muted); font-size:var(--font-auxiliary); }
.source-file-table .file-download-cell { position:sticky; z-index:1; right:0; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.source-file-table thead .file-download-cell { z-index:2; }
.source-file-table tbody tr:hover .file-download-cell { background:rgb(var(--layout-bg-color)); }
.source-file-table tbody tr.selected .file-download-cell { background:rgb(var(--layout-bg-color)); }
.dispatch-download-hint { color:var(--muted); margin:11px 14px 13px; font-size:var(--font-auxiliary); line-height:1.55; }
.dispatch-table { width:100%; border-collapse:collapse; }
.dispatch-table th,.dispatch-table td { border-bottom:1px solid var(--theme-border-color); text-align:left; font-size:var(--font-auxiliary); }
.dispatch-table th { color:var(--theme-on-primary-color); background:var(--theme-table-header-color); font-size:var(--font-auxiliary); letter-spacing:.4px; }
.dispatch-table tbody tr { cursor:default; }
.dispatch-table input[type='checkbox'] { width:15px; height:15px; }
.download-check-cell { width:100%; display:flex; align-items:center; justify-content:center; }
.download-check-cell input { display:block; margin:auto; accent-color:var(--blue); cursor:pointer; }
.download-check-cell input:disabled { cursor:not-allowed; }
.file-name-cell { white-space:nowrap; }
.file-name-cell strong,.file-name-cell small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.file-name-cell strong { color:var(--navy); font-size:var(--font-auxiliary); }
.file-name-cell small { max-width:270px; margin-top:4px; color:var(--text-color-3); font:9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.file-extension { color:var(--text-color-2); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
:deep(.dispatch-status) { display:inline-flex; padding:4px 8px; white-space:nowrap; }
.dispatch-empty-row { padding:32px!important; color:var(--muted); text-align:center!important; }
.dispatch-empty-history { min-height:160px; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:30px; color:var(--muted); text-align:center; }
.dispatch-empty-history i { width:38px; height:38px; display:grid; place-items:center; margin-bottom:10px; border-radius:50%; font-style:normal; font-size:18px; }
.dispatch-empty-history strong { color:var(--text-color-2); font-size:13px; }
.dispatch-empty-history span { margin-top:5px; font-size:var(--font-auxiliary); }
.history-table { min-width:1180px; }
.history-table code { color:var(--blue); font-size:var(--font-auxiliary); }
.history-actions { display:flex; align-items:center; justify-content:center; gap:6px; }
.history-actions button { min-height:30px; padding:0 9px; white-space:nowrap; }
.filename-standard { display:inline-flex; padding:3px 7px; border-radius:999px; white-space:nowrap; font-size:var(--font-auxiliary); color:rgb(var(--success-color)); background:rgb(var(--success-color) / .1); }
@media (max-width:1000px) {
  .dispatch-workspace,.dispatch-workspace.download-workspace,.dispatch-workspace.push-workspace { grid-template-columns:1fr; }
  .directory-scroll { max-height:420px; }
}
@media (max-width:560px) {
  .file-name-search { align-items:stretch; flex-direction:column; gap:7px; }
  .file-name-search > div { width:100%; }
  .download-batch-toolbar { align-items:stretch; flex-direction:column; }
  .download-batch-toolbar > div button { flex:1; }
}

/* 与生产指挥中心统一的文件派发视觉层 */
.dispatch-source-status { display:flex; flex-wrap:wrap; gap:8px; margin:12px 0 0; }
.dispatch-source-status span { display:inline-flex; align-items:center; gap:6px; padding:4px 8px; color:var(--text-color-2); border:1px solid var(--theme-border-color); border-radius:999px; background:rgb(var(--container-bg-color)); font-size:var(--font-auxiliary); }
.dispatch-source-status i { width:7px; height:7px; display:inline-block; flex:0 0 auto; border-radius:50%; background:rgb(var(--warning-color)); box-shadow:var(--theme-shadow); }
.dispatch-source-status i.ready { background:rgb(var(--success-color)); box-shadow:var(--theme-shadow); }
.dispatch-workspace.download-workspace { grid-template-columns:280px minmax(0,1fr); gap:12px; margin-top:12px; align-items:stretch; }
.dispatch-workspace>.panel { margin-bottom:0; }
.dispatch-workspace>.panel,.dispatch-history-panel { overflow:hidden; border:1px solid var(--theme-border-color); border-radius:11px; box-shadow:var(--theme-shadow); animation:surface-in .44s cubic-bezier(.2,.75,.25,1) both; }
.dispatch-files-panel { animation-delay:.05s!important; }
.panel-title { min-height:48px; padding:0 16px; border-bottom:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.panel-title>div { gap:7px; }
.panel-title h2 { color:var(--text-color-1); font-size:14px; }
.panel-title>p { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.dispatch-section-icon { width:27px!important; height:27px!important; display:grid!important; place-items:center; padding:0!important; color:rgb(var(--info-color))!important; border:0!important; border-radius:8px!important; background:rgb(var(--info-color) / .1)!important; font-size:inherit!important; }
.dispatch-section-icon.blue { color:rgb(var(--info-color))!important; background:rgb(var(--info-color) / .1)!important; }
.dispatch-section-icon.cyan { color:rgb(var(--info-color))!important; background:rgb(var(--info-color) / .1)!important; }
.dispatch-section-icon.green { color:rgb(var(--success-color))!important; background:rgb(var(--success-color) / .1)!important; }
.project-search,.file-name-search { border-bottom-color:var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.project-search { padding:16px; gap:8px; }
.directory-panel { position:relative; display:grid; grid-template-columns:minmax(0,1fr); grid-template-rows:auto auto minmax(0,1fr); min-height:0; }
.directory-panel .panel-title>div,.directory-panel .project-search { min-width:0; }
.directory-scroll { grid-area:3 / 1 / 4 / -1; position:absolute; inset:0; min-height:0; max-height:none; overflow:auto; overscroll-behavior:contain; scrollbar-gutter:stable; }
.project-node-row { border-radius:7px; }
.project-node-row:hover { background:var(--theme-hover-color); }
.project-node-row.selected { background:rgb(var(--container-bg-color)); box-shadow:inset 3px 0 rgb(var(--info-color)); }
.project-folder { border-color:#d99a22; background:#f7c85b; }
.project-folder::before { border-color:#d99a22; background:#f7c85b; }
.selected-directory { min-height:39px; padding:12px 16px; border-bottom-color:rgb(var(--primary-color)); background:rgb(var(--container-bg-color)); gap:8px; }
.selected-directory span { color:var(--text-color-3); }
.selected-directory strong { color:rgb(var(--info-color)); font-size:var(--font-auxiliary); }
.file-name-search { padding:16px; gap:8px; }
.file-name-search>div { width:min(355px,100%); }
.file-name-search input { height:34px; border-color:var(--theme-border-color); border-radius:7px; }
.download-batch-toolbar { min-height:44px; padding:12px 16px; border-bottom-color:var(--theme-border-color); background:rgb(var(--container-bg-color)); gap:8px; }
.download-batch-toolbar button { min-height:31px; }
.mobile-selection-bar,.mobile-selection-bar-spacer { display:none; }
.source-file-table { min-width:820px; }
.dispatch-table th,.dispatch-table td { height:39px; padding:7px 9px; border-bottom-color:var(--theme-border-color); }
.source-file-table th { color:var(--text-color-1); background:var(--theme-table-header-color); font-size:var(--font-auxiliary); }
.source-file-table thead .file-download-cell { background:var(--theme-table-header-color); }
.dispatch-table tbody tr:hover { background:rgb(var(--layout-bg-color)); }
.dispatch-table tbody tr.selected { background:rgb(var(--layout-bg-color)); box-shadow:inset 3px 0 rgb(var(--info-color)); }
.file-download-cell { display:inline-flex; align-items:center; gap:8px; max-width:100%; }
.file-download-cell :deep(.n-button) { flex-shrink:0; }
.file-download-cell button { min-height:29px; padding:4px 8px; }
.filename-standard.warning { color:rgb(var(--warning-color)); background:rgb(var(--warning-color) / .1); }
.dispatch-history-panel { --content-table-font-size:13px; --content-table-header-font-size:12px; margin-top:24px; }
.history-table th { color:var(--theme-on-primary-color); background:var(--theme-table-header-color); }
.history-table .dispatch-project-name { font-size:12px; }
.dispatch-empty-history i { color:rgb(var(--info-color)); border:0; background:rgb(var(--info-color) / .1); }
@media (max-width:1000px) {
  .dispatch-workspace.download-workspace { grid-template-columns:1fr; }
  .download-workspace .dispatch-files-panel { min-height:0; }
  .directory-panel { display:flex; flex-direction:column; }
  .directory-scroll { position:static; min-height:220px; max-height:410px; flex:0 1 auto; }
}
@media (max-width:680px) {
  .dispatch-source-status { gap:8px; margin:12px 0 0; }
  .file-name-search { align-items:stretch; gap:8px; padding:16px; }
  .download-batch-toolbar { align-items:stretch; gap:8px; padding:12px 16px; }
  .mobile-selection-bar-spacer { display:block; height:calc(72px + env(safe-area-inset-bottom)); }
  .mobile-selection-bar { position:fixed; z-index:950; right:0; bottom:0; left:0; display:grid; grid-template-columns:auto auto minmax(0,1fr); align-items:center; gap:8px; padding:10px max(12px,env(safe-area-inset-right)) calc(10px + env(safe-area-inset-bottom)) max(12px,env(safe-area-inset-left)); border-top:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
  .mobile-selection-bar>span { min-width:54px; display:flex; align-items:baseline; gap:3px; color:var(--text-color-3); white-space:nowrap; }
  .mobile-selection-bar>span strong { color:rgb(var(--info-color)); font-size:20px; font-variant-numeric:tabular-nums; }
  .mobile-selection-bar>span small { font-size:var(--font-auxiliary); }
  .mobile-selection-bar button { min-height:44px; padding:0 12px; white-space:nowrap; }
  .mobile-selection-bar button.primary { min-width:0; display:flex; align-items:center; justify-content:center; gap:6px; }
  .mobile-selection-bar button:disabled { cursor:not-allowed; }
}
@media (prefers-reduced-motion:reduce) {
  .dispatch-workspace>.panel,.dispatch-history-panel { animation:none; }
}
.dispatch-history-scope { margin:0; padding:12px 16px; border-bottom:1px solid var(--theme-border-color); color:var(--text-color-2); font-size:var(--font-auxiliary); }
.history-table :is(code,.dispatch-status,.history-actions button) { font-size:inherit; }
@media (max-width:560px) {
  .dispatch-history-panel > .dispatch-table-wrap { padding:12px; }
}
</style>
