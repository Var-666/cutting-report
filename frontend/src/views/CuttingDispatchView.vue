<script setup lang="ts">
import AppReportTable from '../components/AppReportTable.vue'
import DispatchHistoryTable from '../components/dispatch/DispatchHistoryTable.vue'
import { useCuttingFileBrowser } from '../composables/useCuttingFileBrowser'
import { useCuttingDispatchHistory } from '../composables/useCuttingDispatchHistory'
import { useCuttingDispatchBatch } from '../composables/useCuttingDispatchBatch'
import type { ReadOptions } from '../types/api'
import type { CuttingDispatch, CuttingFile, CuttingMachine, CuttingStatus } from '../types/cutting'

import { toError } from '../utils/errors'
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAutoComplete, NProgress, NTag } from 'naive-ui'
import CatalogTreeNode from '../components/CatalogTreeNode.vue'
import AppIcon from '../components/AppIcon.vue'
import heroImage from '../assets/home-laser-cutting.webp'
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
  historyError, loadHistory, changeHistoryPage, pauseHistory,
} = useCuttingDispatchHistory({
  pageActive,
  isBusy: () => Boolean(operatingId.value || pushBatch.saving),
})
const {
  bulkQuantity, pushBatch, pushBatchOpen, invalidBatchCount, nonStandardBatchCount,
  overrideBatchCount, batchStatusCounts, batchItemError, hasNameMismatch, openPushBatch,
  closePushBatch, removePushBatchItem, applyBulkQuantity, invalidateBatchConfirmation, batchStatusLabel,
  isStaleFileError, submitPushBatch, retryBatchItem, checkBatchItem, refreshBatchCatalog,
} = useCuttingDispatchBatch({
  selectedFiles,
  clearSelectedFiles,
  dispatchForm,
  systemStatus,
  operatingId,
  loadHistory,
  refreshVisibleFileStatuses,
  loadFiles,
  showNotice,
})

const selectedFileCount = computed(() => selectedFiles.value.size)
const selectedFileItems = computed(() => [...selectedFiles.value.values()])
const selectedMachine = computed(() => machines.value.find((machine) => machine.machine_ip === dispatchForm.machineIp))

function machineName(machineIp: string) {
  const names: Record<string, string> = {
    '10.147.1.40': '20kW 切割机',
    '10.147.1.38': '30kW 切割机',
  }
  return names[machineIp] || machines.value.find((machine) => machine.machine_ip === machineIp)?.server_name || '未命名机床'
}

const activeDispatchStep = computed(() => {
  if (pushBatch.resultsReady) return 4
  if (pushBatch.visible) return 3
  if (selectedFileCount.value) return 2
  return 1
})

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
    await Promise.allSettled([loadHistory(), refreshVisibleFileStatuses()])
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
      loadHistory({ signal }),
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
  initializePage()
})

onActivated(async () => {
  pageActive.value = true
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
  { key: 'c9', title: "派发状态", width: 112, align: 'center' },
  { key: 'c10', title: "操作", width: 148, align: 'center', fixed: 'right', ellipsis: false }
] as const

const batchTableColumns = [
  { key: 'c0', title: "文件 / 识别结果", minWidth: 220, align: 'left', fixed: 'left' },
  { key: 'c1', title: "分段名", width: 140, align: 'left', ellipsis: false },
  { key: 'c2', title: "材质", width: 120, align: 'left', ellipsis: false },
  { key: 'c3', title: "板厚（mm）", width: 104, align: 'right', ellipsis: false },
  { key: 'c4', title: "数量", width: 88, align: 'right', ellipsis: false },
  { key: 'c5', title: "状态", width: 200, align: 'center' },
  { key: 'c6', title: "操作", width: 260, align: 'center', fixed: 'right', ellipsis: false }
] as const
</script>

<template>
  <section class="page-hero dispatch-hero" :style="{ '--dispatch-hero-image': `url(${heroImage})` }">
    <div class="dispatch-hero__copy">
      <span class="eyebrow">CUTTING FILE DISPATCH</span>
      <h1>文件派发</h1>
      <div class="dispatch-source-status" aria-label="文件派发服务状态">
        <span><i :class="{ ready: systemStatus?.can_push }"></i>{{ systemStatus?.pdm?.label || (statusError ? '数据源检查失败' : '正在检查数据源') }}</span>
        <span>{{ systemStatus?.file_source?.label || (statusError ? '文件服务状态未确认' : '文件服务检查中') }}</span>
        <span>{{ systemStatus?.safe_mode ? '安全联调模式' : '生产数据模式' }}</span>
      </div>
    </div>
  </section>

  <div class="dispatch-flow" aria-label="文件派发流程">
    <div v-for="step in [{ number: 1, title: '选择项目目录' }, { number: 2, title: '选择文件' }, { number: 3, title: '确认派发' }, { number: 4, title: '完成派发' }]" :key="step.number" :class="{ active: activeDispatchStep === step.number, complete: activeDispatchStep > step.number }">
      <i><AppIcon v-if="activeDispatchStep > step.number" name="check" size="17" /> <template v-else>{{ step.number }}</template></i><span><strong>{{ step.title }}</strong></span>
    </div>
  </div>

  <div v-if="statusError" class="dispatch-notice error" role="alert">
    <span>{{ statusError }}</span>
    <NButton attr-type="button" :disabled="statusLoading" @click="loadRuntimeStatus({ force: true })">重新检查</NButton>
  </div>
  <div v-if="notice" class="dispatch-notice" :class="{ error: noticeType === 'error' }" role="status">
    <span>{{ notice }}</span>
    <NButton attr-type="button" @click="notice = ''">关闭</NButton>
  </div>

  <div class="dispatch-workspace download-workspace">
    <aside class="panel directory-panel">
      <div class="panel-title">
        <div><span class="dispatch-section-icon"><AppIcon name="briefcase" size="17" /></span><small>01</small><h2>项目目录</h2></div>
        <p>{{ projectOptions.length }} 个支持项目</p>
      </div>
      <div class="project-search">
        <label for="project-selector">支持项目 <small>输入项目名称或船号搜索</small></label>
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
        <div><span class="dispatch-section-icon blue"><AppIcon name="file" size="17" /></span><small>02</small><h2>目录文件</h2></div>
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
        <div><NButton attr-type="button" :disabled="!selectedFileCount || pushBatch.saving" @click="clearSelectedFiles">清空选择</NButton><NButton class="primary" attr-type="button" :disabled="!selectedFileCount || loading || pushBatch.saving || !systemStatus?.can_push" @click="openPushBatch" type="primary">设置参数并派发</NButton></div>
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
        <template #c10="{ row }"><div class="file-download-cell"><NButton attr-type="button" :disabled="loading || pushBatch.saving || !row.path_available || !systemStatus?.can_push" :aria-label="`派发 ${row.document_no}`" @click="openSinglePush(row)">设置并派发</NButton><small v-if="!row.path_available">{{ row.path_error || '路径不可用' }}</small></div></template>
      <template #empty>{{ fileError ? '文件读取失败，请重试' : selectedCatalog ? appliedFileKeyword ? '当前目录没有匹配的文件' : '当前目录没有文件' : '请从左侧选择一个目录' }}</template>
      </AppReportTable>
      </div>
      <p v-if="systemStatus && !systemStatus.can_push" class="dispatch-download-hint">当前文件来源或魔盒服务不可用，暂不可派发。</p>
    </section>

    <aside class="panel dispatch-confirm-panel">
      <div class="panel-title">
        <div><span class="dispatch-section-icon cyan"><AppIcon name="send" size="17" /></span><small>03</small><h2>派发确认</h2></div>
      </div>
      <div class="dispatch-confirm-content">
        <span class="confirm-label">已选择文件</span>
        <p class="confirm-total"><strong>{{ selectedFileCount }}</strong> 项</p>
        <div class="confirm-file-list" :class="{ empty: !selectedFileCount }">
          <template v-if="selectedFileCount">
            <span v-for="file in selectedFileItems.slice(0, 5)" :key="fileKey(file)" :title="file.document_no || file.physical_file_name || undefined">{{ file.document_no || file.physical_file_name }}</span>
            <NButton v-if="selectedFileCount > 5" attr-type="button" @click="openPushBatch">其余 {{ selectedFileCount - 5 }} 项 · 展开全部</NButton>
          </template>
          <template v-else><AppIcon name="file" size="23" /><span>请从目录文件中选择待派发文件</span></template>
        </div>
        <div class="confirm-machine">
          <label for="dispatch-machine"><AppIcon name="settings" size="16" />目标机床</label>
          <AppSelect id="dispatch-machine" v-model="dispatchForm.machineIp" :disabled="!machines.length || pushBatch.saving"><option value="" disabled>请选择目标机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machineName(machine.machine_ip) }} · {{ machine.machine_ip }}（{{ machine.online ? '在线' : '待确认' }}）</option></AppSelect>
          <small><i :class="{ ready: selectedMachine?.online }"></i>{{ selectedMachine ? `${machineName(selectedMachine.machine_ip)} · ${selectedMachine.online ? '机床在线' : '未从列表确认在线'}` : systemStatus?.file_source?.label || '请选择接收文件的机床' }}</small>
        </div>
        <NButton class="confirm-dispatch-button" attr-type="button" :disabled="!selectedFileCount || loading || pushBatch.saving || !systemStatus?.can_push || !dispatchForm.machineIp" @click="openPushBatch"><AppIcon name="send" size="18" tone="light" />设置参数并派发</NButton>
      </div>
    </aside>
  </div>

  <section class="panel dispatch-history-panel" :aria-busy="historyLoading">
    <div class="panel-title"><div><span class="dispatch-section-icon green"><AppIcon name="check" size="17" /></span><small>04</small><h2>派发记录</h2></div></div>
    <div v-if="historyError" class="global-error" role="alert"><span>派发记录读取失败：{{ historyError }}</span><NButton attr-type="button" :disabled="historyLoading" @click="loadHistory()">重试</NButton></div>
    <div v-if="historyLoading && !histories.length" class="dispatch-empty-history" role="status"><span class="spinner dark" aria-hidden="true"></span><strong>正在读取派发记录</strong></div>
    <div v-else-if="histories.length" class="dispatch-table-wrap"><DispatchHistoryTable :histories="histories" :history-total="historyTotal" :history-page="historyPage" :history-page-size="historyPageSize" :project-options="projectOptions" :machine-name="machineName" :loading="historyLoading || !!operatingId || pushBatch.saving" @page="changeHistoryPage" /></div>
    <div v-else-if="!historyError" class="dispatch-empty-history"><i><AppIcon name="send" size="20" /></i><strong>暂无派发记录</strong></div>

  </section>

  <div v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar-spacer" aria-hidden="true"></div>
  <aside v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar" aria-label="已选文件操作">
    <span><small>已选</small><strong>{{ selectedFileCount }}</strong><small>/ 100 项</small></span>
    <NButton attr-type="button" :disabled="pushBatch.saving" @click="clearSelectedFiles">清空</NButton>
    <NButton class="primary" attr-type="button" :disabled="loading || pushBatch.saving || !systemStatus?.can_push" @click="openPushBatch" type="primary"><AppIcon name="send" size="16" tone="light" />设置参数并派发</NButton>
  </aside>

    <NModal :show="!!retryDispatch" @update:show="visible => { if (!visible) retryDispatch = null }">
      <div class="retry-dispatch-dialog panel" role="dialog" aria-modal="true" aria-labelledby="retry-dispatch-title"><form @submit.prevent="retryDispatch && executeSaved(retryDispatch, retrySegment.trim())">
        <h2 id="retry-dispatch-title">补充分段名</h2>
        <p>{{ retryDispatch?.document_no }} 为非标准文件名，请填写分段名后核对执行。</p>
        <label><span>分段名</span><NInput v-model:value.trim="retrySegment" :maxlength="80" :input-props="{ name: 'retry_segment', required: true }" placeholder="例如：806C" /></label>
        <footer><NButton @click="retryDispatch = null">取消</NButton><NButton type="primary" attr-type="submit" :disabled="!retrySegment.trim()">核对并继续</NButton></footer>
      </form></div>
    </NModal>
    <NModal :show="pushBatchOpen" :mask-closable="!pushBatch.saving" :close-on-esc="!pushBatch.saving" @update:show="visible => { if (!visible) closePushBatch() }">
      <div class="push-batch-dialog" role="dialog" aria-modal="true" aria-labelledby="push-batch-title" tabindex="-1">
        <header class="push-batch-header">
          <div><span>FILE DISPATCH</span><h2 id="push-batch-title">{{ pushBatch.items.length === 1 ? '文件派发确认' : '批量派发确认' }}</h2></div>
          <NButton attr-type="button" :disabled="pushBatch.saving" aria-label="关闭批量推送窗口" @click="closePushBatch">×</NButton>
        </header>

        <div class="push-batch-content">
          <div class="push-batch-common">
            <label><span>目标机床</span><AppSelect v-model="dispatchForm.machineIp" :disabled="pushBatch.saving || pushBatch.resultsReady"><option value="" disabled>请选择目标机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machineName(machine.machine_ip) }} · {{ machine.machine_ip }}（{{ machine.online ? '在线' : '未从列表确认在线' }}）</option></AppSelect></label>
            <label><span>统一加工数量</span><div><NInputNumber v-model:value="bulkQuantity" name="bulk_quantity" :min="1" :max="10000" :step="1" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" :input-props="{ 'name': 'bulk_quantity', 'autocomplete': 'off' }" :precision="0" /><NButton attr-type="button" :disabled="pushBatch.saving || pushBatch.resultsReady" @click="applyBulkQuantity">应用到全部</NButton></div></label>
            <div class="push-batch-summary"><strong>{{ pushBatch.items.length }}</strong><span>个文件</span><small v-if="!pushBatch.resultsReady">{{ invalidBatchCount ? `${invalidBatchCount} 项待补参数` : nonStandardBatchCount || overrideBatchCount ? `${nonStandardBatchCount} 项非标准名、${overrideBatchCount} 项参数不符待确认` : '全部为标准名' }}</small><small v-else>已推送 {{ batchStatusCounts.sent }} · 失败 {{ batchStatusCounts.failed }} · 重复 {{ batchStatusCounts.duplicate }}</small></div>
          </div>

          <div v-if="pushBatch.error" class="push-batch-error" role="alert">{{ pushBatch.error }}</div>
          <div v-if="pushBatch.saving" class="push-batch-progress" role="status"><div><strong>{{ pushBatch.currentFilename }}</strong><span>{{ pushBatch.completed }} / {{ pushBatch.total }}</span></div><NProgress type="line" :percentage="pushBatch.total ? Math.round(pushBatch.completed / pushBatch.total * 100) : 0" :show-indicator="false" /><small>正在按顺序处理，请不要关闭页面。</small></div>

          <div class="push-batch-table-wrap">
            <AppReportTable class="push-batch-table" :columns="batchTableColumns" :items="pushBatch.items" :row-key="item => item.referenceKey" :paginate="false" :row-class-name="item => (!pushBatch.resultsReady && (batchItemError(item) || !item.parsed || hasNameMismatch(item))) ? 'invalid' : ''" :loading="pushBatch.saving">
        <template #c0="{ row: item }"><div class="push-batch-file"><strong>{{ item.filename }}</strong><small>版本 {{ item.version }}</small><span v-if="item.parsed" class="recognized">已识别：{{ item.parsed.material }} · {{ item.parsed.thickness.toFixed(1) }} mm</span><span v-else class="manual">非标准名，请手工填写参数</span><span v-if="hasNameMismatch(item)" class="manual">加工参数与标准名不一致，请人工复核</span></div></template>
        <template #c1="{ row: item }"><span v-if="item.parsed">{{ item.segmentName }}</span><NInput v-else v-model:value.trim="item.segmentName" :name="`segment_${item.referenceKey}`" :aria-label="`${item.filename} 分段名`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" :maxlength="80" placeholder="例如：806C" @update:value="invalidateBatchConfirmation(item)" :input-props="{ 'name': `segment_${item.referenceKey}`, 'aria-label': `${item.filename} 分段名`, 'autocomplete': 'off' }" /></template>
        <template #c2="{ row: item }"><NInput v-model:value.trim="item.material" :name="`material_${item.referenceKey}`" :aria-label="`${item.filename} 材质`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" :maxlength="80" placeholder="例如：A0、B0、SS" @update:value="invalidateBatchConfirmation(item)" :input-props="{ 'name': `material_${item.referenceKey}`, 'aria-label': `${item.filename} 材质`, 'autocomplete': 'off' }" /></template>
        <template #c3="{ row: item }"><NInputNumber :value="item.thickness === '' ? null : Number(item.thickness)" @update:value="item.thickness = $event ?? ''; invalidateBatchConfirmation(item)" :name="`thickness_${item.referenceKey}`" :aria-label="`${item.filename} 板厚`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" inputmode="decimal" :min="0.01" :max="1000" :step="0.01" placeholder="例如：12.0…" :input-props="{ 'name': `thickness_${item.referenceKey}`, 'aria-label': `${item.filename} 板厚`, 'autocomplete': 'off', 'inputmode': 'decimal' }" /></template>
        <template #c4="{ row: item }"><NInputNumber v-model:value="item.quantity" :name="`quantity_${item.referenceKey}`" :aria-label="`${item.filename} 数量`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" inputmode="numeric" :min="1" :max="10000" :step="1" @update:value="invalidateBatchConfirmation(item)" :input-props="{ 'name': `quantity_${item.referenceKey}`, 'aria-label': `${item.filename} 数量`, 'autocomplete': 'off', 'inputmode': 'numeric' }" :precision="0" /></template>
        <template #c5="{ row: item }"><div class="push-batch-result"><span :class="item.status">{{ batchStatusLabel(item) }}</span><small v-if="item.dispatchId" :title="item.dispatchId">任务 {{ item.dispatchId }}</small><small v-if="item.error" :title="item.error">{{ item.error }}</small></div></template>
        <template #c6="{ row: item }"><div class="push-batch-actions"><template v-if="pushBatch.resultsReady"><NButton v-if="item.status === 'failed' && isStaleFileError(item)" attr-type="button" :disabled="pushBatch.saving" @click="refreshBatchCatalog">刷新清单重选</NButton><NButton v-else-if="item.status === 'failed' && (!item.dispatchId || item.retryable)" attr-type="button" :disabled="pushBatch.saving" @click="retryBatchItem(item)">{{ item.dispatchId ? '重试当前任务' : '重试创建并派发' }}</NButton><NButton v-if="item.status === 'duplicate' && item.dispatchId && (!item.statusChecked || item.retryable)" attr-type="button" :disabled="pushBatch.saving" @click="retryBatchItem(item)">核对并继续已有任务</NButton><NButton v-if="item.dispatchId && (item.status === 'failed' || item.status === 'duplicate')" attr-type="button" :disabled="pushBatch.saving" @click="checkBatchItem(item)">核对状态</NButton><span v-if="item.status === 'sent'">已完成</span></template><NButton v-else class="push-batch-remove" attr-type="button" :disabled="pushBatch.saving" @click="removePushBatchItem(item)">移除</NButton></div></template>
      </AppReportTable>
          </div>
          <div v-if="!pushBatch.resultsReady" class="push-batch-safety">
            <p>标准指令名已自动识别分段并填充材质和板厚，请在提交前逐项核对。非标准名不会猜测参数，必须手工补全。</p>
            <NCheckbox v-model:checked="pushBatch.nonStandardConfirmed" name="non_standard_confirmed" :disabled="pushBatch.saving"  v-if="nonStandardBatchCount">我已人工核对 {{ nonStandardBatchCount }} 个非标准文件名及其分段、材质、板厚和数量</NCheckbox>
            <NCheckbox v-model:checked="pushBatch.overrideConfirmed" name="standard_override_confirmed" :disabled="pushBatch.saving"  v-if="overrideBatchCount">我已人工核对 {{ overrideBatchCount }} 个与标准名不一致的材质或板厚</NCheckbox>
          </div>
        </div>

        <footer class="push-batch-footer">
          <template v-if="pushBatch.resultsReady"><span>可重试安全失败项；上传结果待核对的任务须先检查魔盒，系统不会自动重复上传。</span><NButton class="primary" attr-type="button" :disabled="pushBatch.saving" @click="closePushBatch" type="primary">完成</NButton></template>
          <template v-else><div><NButton attr-type="button" :disabled="pushBatch.saving" @click="closePushBatch">取消</NButton><NButton class="primary" attr-type="button" :disabled="pushBatch.saving || invalidBatchCount > 0 || !dispatchForm.machineIp || !systemStatus?.can_push || (nonStandardBatchCount > 0 && !pushBatch.nonStandardConfirmed) || (overrideBatchCount > 0 && !pushBatch.overrideConfirmed)" @click="submitPushBatch" type="primary">{{ pushBatch.saving ? '顺序派发中…' : pushBatch.items.length === 1 ? '确认派发' : '确认并顺序派发' }}</NButton></div></template>
        </footer>
      </div>
    </NModal>
</template>

<style scoped>
.retry-dispatch-dialog{width:min(480px,calc(100vw - 32px));padding:24px}.retry-dispatch-dialog footer{display:flex;justify-content:flex-end;gap:10px;margin-top:20px}

.dispatch-project-name { display:block; color:var(--muted); font-size:var(--font-auxiliary); max-width:140px; overflow-wrap:anywhere; margin-top:5px; }
.dispatch-stage-badge { min-width:310px; display:flex; flex-direction:column; padding:17px 20px; border:1px solid var(--theme-border-color); border-right:4px solid var(--theme-border-color); background:rgb(var(--warning-color) / .1); box-shadow:var(--theme-shadow); }
.dispatch-stage-badge span { color:rgb(var(--warning-color)); font-size:var(--font-auxiliary); font-weight:700; letter-spacing:2px; }
.dispatch-stage-badge strong { margin:4px 0; color:var(--text-color-1); font-size:13px; }
.dispatch-stage-badge small { color:var(--text-color-3); }
.dispatch-flow { display:grid; margin-bottom:22px; border:1px solid var(--line); background:rgb(var(--container-bg-color)); }
.dispatch-flow > div { position:relative; min-height:82px; display:flex; align-items:center; gap:12px; padding:17px 20px; border-right:1px solid var(--line); }
.dispatch-flow > div:last-child { border-right:0; }
.dispatch-flow > div::after { content:'›'; position:absolute; right:-7px; z-index:1; color:var(--text-color-3); background:rgb(var(--container-bg-color)); font-size:24px; }
.dispatch-flow > div:last-child::after { display:none; }
.dispatch-flow i { display:grid; place-items:center; flex:0 0 auto; border:1px solid var(--theme-border-color); border-radius:50%; font-style:normal; }
.dispatch-flow span { display:flex; flex-direction:column; }
.dispatch-flow .active i { color:var(--theme-on-primary-color); }
.dispatch-notice { display:flex; align-items:center; gap:12px; margin-bottom:22px; padding:13px 16px; color:var(--text-color-1); background:rgb(var(--info-color) / .1); font-size:12px; }
.dispatch-notice.error { color:rgb(var(--error-color)); background:rgb(var(--error-color) / .1); }
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
.download-batch-toolbar > span small { font-size:inherit; }
.download-batch-toolbar > span { color:var(--muted); font-size:var(--font-auxiliary); }
.download-batch-toolbar > span strong { margin:0 3px; color:var(--blue); font-size:15px; font-variant-numeric:tabular-nums; }
.download-batch-toolbar > span small { margin-left:10px; color:rgb(var(--warning-color)); font-size:inherit; }
.download-batch-toolbar > div { display:flex; gap:7px; }
.download-batch-toolbar button { padding:0 12px; white-space:nowrap; }
.download-batch-toolbar button:disabled { cursor:not-allowed; }
.dispatch-table-wrap { overflow-x:auto; position:relative; min-height:75px; }
.dispatch-history-panel > .dispatch-table-wrap { min-width:0; padding:16px; }
.dispatch-loading { position:absolute; z-index:2; inset:0; display:flex; align-items:flex-start; justify-content:center; padding-top:48px; background:rgb(var(--info-color) / .1); color:var(--blue); font-size:12px; }.file-download-cell button { white-space:nowrap; cursor:pointer; }.file-download-cell button:disabled { cursor:not-allowed; }.file-download-cell small { display:block; margin-top:4px; color:var(--muted); font-size:var(--font-auxiliary); }
.source-file-table .file-download-cell { position:sticky; z-index:1; right:0; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); }
.source-file-table thead .file-download-cell { z-index:2; }
.source-file-table tbody tr:hover .file-download-cell { background:rgb(var(--layout-bg-color)); }
.source-file-table tbody tr.selected .file-download-cell { background:rgb(var(--layout-bg-color)); }
.dispatch-download-hint { color:var(--muted); }
.dispatch-table { width:100%; border-collapse:collapse; }
.dispatch-table th,.dispatch-table td { border-bottom:1px solid var(--theme-border-color); text-align:left; font-size:var(--font-auxiliary); }
.dispatch-table th { color:var(--theme-on-primary-color); background:var(--theme-table-header-color); font-size:var(--font-auxiliary); letter-spacing:.4px; }
.dispatch-table tbody tr { cursor:default; }
.dispatch-table input[type='checkbox'] { width:15px; height:15px; }
.download-check-cell { width:100%; display:flex; align-items:center; justify-content:center; }
.download-check-cell input { display:block; margin:auto; accent-color:var(--blue); cursor:pointer; }
.download-check-cell input:disabled { cursor:not-allowed; }
.check-column { width:42px; text-align:center!important; }
.file-name-cell { max-width:230px; }
.file-name-cell strong,.file-name-cell small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.file-name-cell strong { color:var(--navy); font-size:var(--font-auxiliary); }
.file-name-cell small { max-width:270px; margin-top:4px; color:var(--text-color-3); font:9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.file-extension { color:var(--text-color-2); font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.push-file-picker { min-width:0; display:flex; flex-direction:column; }
.push-file-filter { display:grid; grid-template-columns:minmax(180px,1.2fr) minmax(150px,.9fr) minmax(190px,1.1fr) auto; align-items:end; gap:10px; padding:16px 18px; border-bottom:1px solid var(--theme-border-color); background:var(--theme-hover-color); }
.push-file-filter label > span { display:block; margin-bottom:6px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.push-file-filter input,.push-file-filter .app-select { width:100%; min-width:0; }
.push-filter-actions { display:flex; justify-content:flex-end; gap:7px; }
.push-filter-actions button { min-height:38px; padding:0 13px; }
.push-filter-actions .primary { color:var(--theme-on-primary-color); border-color:var(--blue); background:var(--blue); }
.push-file-table tbody tr { cursor:pointer; }
.push-check-cell { width:48px; text-align:center!important; }
.push-check-cell input { display:block; width:15px; height:15px; margin:auto; accent-color:var(--blue); cursor:pointer; }
.push-file-origin { max-width:245px; }
.push-file-origin strong,.push-file-origin small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.push-file-origin strong { color:var(--navy); font-size:var(--font-auxiliary); }
.push-file-origin small { max-width:230px; margin-top:4px; color:var(--muted); font-size:var(--font-auxiliary); }
.push-file-picker .dispatch-table-wrap { min-height:210px; flex:1 1 auto; }
:deep(.dispatch-status) { display:inline-flex; padding:4px 8px; white-space:nowrap; }
.dispatch-empty-row { padding:32px!important; color:var(--muted); text-align:center!important; }
.dispatch-pagination { display:flex; align-items:center; justify-content:flex-end; gap:12px; color:var(--muted); font-size:var(--font-auxiliary); }
.dispatch-pagination button { min-height:30px; padding:0 12px; }
.dispatch-pagination button:disabled { cursor:not-allowed; }
.dispatch-empty-history { min-height:160px; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:30px; color:var(--muted); text-align:center; }
.dispatch-empty-history i { width:38px; height:38px; display:grid; place-items:center; margin-bottom:10px; border-radius:50%; font-style:normal; font-size:18px; }
.dispatch-empty-history strong { color:var(--text-color-2); font-size:13px; }
.dispatch-empty-history span { margin-top:5px; font-size:var(--font-auxiliary); }
.history-table { min-width:1180px; }
.history-table code { color:var(--blue); font-size:var(--font-auxiliary); }
.history-actions { display:flex; align-items:center; justify-content:center; gap:6px; }
.history-actions button { min-height:30px; padding:0 9px; white-space:nowrap; }
.push-batch-backdrop { position:fixed; z-index:1000; inset:0; display:grid; place-items:center; padding:24px; background:rgb(var(--inverted-bg-color) / .6); }
.push-batch-dialog { width:min(1180px,100%); max-height:calc(100vh - 48px); display:flex; flex-direction:column; overflow:hidden; color:var(--text-color-1); background:rgb(var(--container-bg-color)); }
.push-batch-header { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; padding:20px 24px; }
.push-batch-header span { color:rgb(var(--info-color)); font-size:var(--font-auxiliary); font-weight:700; letter-spacing:2px; }
.push-batch-header h2 { margin:3px 0 4px; font-size:20px; }
.push-batch-header p { margin:0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.push-batch-header button { width:34px; min-height:34px; flex:0 0 34px; padding:0; line-height:1; }
.push-batch-content { min-height:0; overflow:auto; padding:20px 24px; }
.push-batch-common { display:grid; grid-template-columns:minmax(280px,1.2fr) minmax(230px,.8fr) minmax(170px,.55fr); align-items:end; gap:16px; margin-bottom:18px; padding:16px; }
.push-batch-common label > span { display:block; margin-bottom:7px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.push-batch-common label > div { display:flex; gap:7px; }
.push-batch-common input,.push-batch-common .app-select { width:100%; min-width:0; }
.push-batch-common button { flex:0 0 auto; min-height:38px; padding:0 11px; }
.push-batch-summary { min-height:61px; display:flex; align-items:baseline; align-content:center; flex-wrap:wrap; gap:4px; padding:10px 13px; border-left:3px solid var(--cyan); background:rgb(var(--info-color) / .1); }
.push-batch-summary strong { color:var(--blue); font-size:22px; }
.push-batch-summary span { font-size:var(--font-auxiliary); }
.push-batch-summary small { width:100%; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.push-batch-error { margin-bottom:14px; padding:10px 13px; color:rgb(var(--error-color)); border-left:3px solid rgb(var(--error-color)); background:rgb(var(--error-color) / .1); font-size:var(--font-auxiliary); }
.push-batch-progress { margin-bottom:14px; padding:11px 13px; background:rgb(var(--info-color) / .1); }
.push-batch-progress > div { display:flex; align-items:center; justify-content:space-between; gap:15px; }
.push-batch-progress strong { min-width:0; overflow:hidden; color:var(--text-color-1); text-overflow:ellipsis; white-space:nowrap; font-size:var(--font-auxiliary); }
.push-batch-progress span,.push-batch-progress small { color:var(--text-color-3); font-size:var(--font-auxiliary); }
.push-batch-progress progress { width:100%; height:7px; margin:8px 0 4px; accent-color:var(--cyan); }
.push-batch-table-wrap { overflow:auto; max-height:410px; border:1px solid var(--theme-border-color); }
.push-batch-table { width:100%; min-width:920px; border-collapse:collapse; }
.push-batch-table th,.push-batch-table td { padding:10px; border-bottom:1px solid var(--theme-border-color); text-align:left; font-size:var(--font-auxiliary); vertical-align:middle; }
.push-batch-table th { position:sticky; z-index:1; top:0; color:var(--theme-on-primary-color); background:var(--theme-table-header-color); font-size:var(--font-auxiliary); }
.push-batch-table tr.invalid { background:rgb(var(--warning-color) / .1); }
.push-batch-table input { width:100%; min-width:82px; height:34px; padding:0 9px; }
.push-batch-file { width:290px; }
.push-batch-file strong,.push-batch-file small,.push-batch-file span { display:block; }
.push-batch-file strong { max-width:280px; overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:var(--font-auxiliary); }
.push-batch-file small { margin-top:3px; color:var(--muted); }
.push-batch-file span { margin-top:5px; font-size:var(--font-auxiliary); }
.push-batch-file .recognized { color:rgb(var(--info-color)); }
.push-batch-file .manual { color:rgb(var(--warning-color)); }
.push-batch-result { width:170px; margin-inline:auto; }
.push-batch-result > span { display:inline-flex; padding:4px 7px; color:rgb(var(--warning-color)); border-radius:999px; background:rgb(var(--warning-color) / .1); white-space:nowrap; }
.push-batch-result > span.sent,.push-batch-result > span.saved { color:var(--text-color-1); background:rgb(var(--success-color) / .1); }
.push-batch-result > span.failed { color:rgb(var(--error-color)); background:rgb(var(--error-color) / .1); }
.push-batch-result > span.pushing { color:var(--text-color-1); background:rgb(var(--info-color) / .1); }
.push-batch-result > span.duplicate { color:rgb(var(--warning-color)); background:rgb(var(--warning-color) / .1); }
.push-batch-result small { display:block; max-width:220px; margin-top:5px; color:var(--text-color-2); overflow-wrap:anywhere; }
.push-batch-remove { min-height:30px; padding:0 8px; color:var(--text-color-2); border:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.push-batch-actions { min-width:140px; display:flex; align-items:center; justify-content:center; gap:6px; }
.push-batch-actions button { display:block; min-height:30px; margin:3px 0; padding:0 8px; }
.push-batch-actions button:disabled { cursor:not-allowed; }
.push-batch-actions span { color:var(--text-color-1); font-size:var(--font-auxiliary); }
.push-batch-safety { margin:14px 0 0; color:rgb(var(--success-color)); font-size:var(--font-auxiliary); line-height:1.7; }
.push-batch-safety p { margin:0; }
.push-batch-safety label { display:flex; align-items:center; gap:7px; margin-top:8px; color:rgb(var(--success-color)); font-weight:700; }
.push-batch-safety input { width:15px; height:15px; accent-color:var(--blue); }
.filename-standard { display:inline-flex; padding:3px 7px; border-radius:999px; white-space:nowrap; font-size:var(--font-auxiliary); }
.push-batch-footer { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:15px 24px; border-top:1px solid var(--theme-border-color); background:rgb(var(--info-color) / .1); }
.push-batch-footer > span { color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.5; }
.push-batch-footer > div { display:flex; flex:0 0 auto; gap:8px; margin-left:auto; }
.push-batch-footer button { min-height:38px; padding:0 16px; white-space:nowrap; }
.push-batch-footer button:disabled,.push-batch-common button:disabled { cursor:not-allowed; }
@media (max-width:1000px) {
  .dispatch-hero { align-items:flex-start; flex-direction:column; justify-content:center; padding:28px 0; }
  .dispatch-stage-badge { width:100%; min-width:0; }
  .dispatch-flow { grid-template-columns:1fr 1fr; }
  .dispatch-flow > div:nth-child(2) { border-right:0; }
  .dispatch-flow > div:nth-child(-n+2) { border-bottom:1px solid var(--line); }
  .dispatch-workspace,.dispatch-workspace.download-workspace,.dispatch-workspace.push-workspace { grid-template-columns:1fr; }
  .directory-scroll { max-height:420px; }
  .push-file-filter { grid-template-columns:1fr 1fr; }
  .push-batch-common { grid-template-columns:1fr 1fr; }
  .push-batch-summary { grid-column:1/-1; }
}
@media (max-width:560px) {
  .dispatch-flow { grid-template-columns:1fr; }
  .dispatch-flow > div { border-right:0; border-bottom:1px solid var(--line); }
  .dispatch-flow > div:last-child { border-bottom:0; }
  .dispatch-flow > div::after { display:none; }
  .file-name-search { align-items:stretch; flex-direction:column; gap:7px; }
  .file-name-search > div { width:100%; }
  .download-batch-toolbar { align-items:stretch; flex-direction:column; }
  .download-batch-toolbar > div button { flex:1; }
  .push-file-filter { grid-template-columns:1fr; }
  .push-filter-actions button { flex:1; }
  .push-batch-backdrop { padding:0; }
  .push-batch-dialog { width:100%; max-height:100vh; min-height:100vh; }
  .push-batch-header,.push-batch-content,.push-batch-footer { padding-left:15px; padding-right:15px; }
  .push-batch-common { grid-template-columns:1fr; gap:16px; padding:16px; }
  .push-batch-summary { grid-column:auto; }
  .push-batch-footer { align-items:stretch; flex-direction:column; }
  .push-batch-footer > div { display:grid; grid-template-columns:1fr 1fr; margin-left:0; }
  .push-batch-footer > div .primary { grid-column:1/-1; }
}

/* 与生产指挥中心统一的文件派发视觉层 */
.dispatch-hero { position:relative; min-height:160px; display:grid; grid-template-columns:minmax(460px,1fr) 330px; align-items:center; overflow:hidden; padding:24px; border:1px solid var(--theme-border-color); border-radius:14px; background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.98) 0%,rgb(var(--container-bg-color) / 0.9) 43%,rgb(var(--container-bg-color) / 0.04) 70%),var(--dispatch-hero-image) center 53%/cover no-repeat; box-shadow:var(--theme-shadow); animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both; gap:24px; }
.dispatch-hero::after { content:""; position:absolute; inset:0; pointer-events:none; background:transparent; }
.dispatch-hero__copy { position:relative; z-index:1; }
.dispatch-hero .eyebrow { display:block; margin:0 0 4px; color:rgb(var(--info-color)); font-size:var(--font-auxiliary); font-weight:700; letter-spacing:1.4px; }
.dispatch-hero h1 { margin:0 0 7px; color:var(--text-color-1); font-size:var(--font-page-title); line-height:1.08; letter-spacing:1px; }
.dispatch-source-status { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; margin:12px 0 0; }
.dispatch-source-status span { display:inline-flex; align-items:center; gap:6px; padding:4px 8px; color:var(--text-color-2); border:1px solid var(--theme-border-color); border-radius:999px; background:rgb(var(--container-bg-color)); font-size:var(--font-auxiliary); }
.dispatch-source-status i,.confirm-machine small i { width:7px; height:7px; display:inline-block; flex:0 0 auto; border-radius:50%; background:rgb(var(--warning-color)); box-shadow:var(--theme-shadow); }
.dispatch-source-status i.ready,.confirm-machine small i.ready { background:rgb(var(--success-color)); box-shadow:var(--theme-shadow); }
.dispatch-flow { grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; overflow:hidden; margin:16px 0 0; border-color:var(--theme-border-color); border-radius:11px; box-shadow:var(--theme-shadow); padding:16px; }
.dispatch-flow>div { min-height:64px; gap:13px; padding:12px 28px; border-right:0; background:rgb(var(--container-bg-color)); }
.dispatch-flow>div::after { content:""; width:82px; height:1px; right:-39px; background:rgb(var(--container-bg-color)); }
.dispatch-flow i { width:32px; height:32px; color:var(--text-color-3); border-color:var(--theme-border-color); font-size:14px; font-weight:700; }
.dispatch-flow strong { color:var(--text-color-1); font-size:12px; }
.dispatch-flow .active { background:rgb(var(--container-bg-color)); }
.dispatch-flow .active i { border-color:rgb(var(--primary-color)); background:rgb(var(--primary-color)); box-shadow:var(--theme-shadow); }
.dispatch-flow .complete i { color:var(--theme-on-primary-color); border-color:var(--theme-border-color); background:rgb(var(--info-color)); }
.dispatch-notice { margin:10px 0 0; border:1px solid var(--theme-border-color); border-left:3px solid var(--theme-border-color); border-radius:8px; }
.dispatch-notice.error { border-color:rgb(var(--error-color)); border-left-color:rgb(var(--error-color)); }
.dispatch-workspace.download-workspace { grid-template-columns:280px minmax(0,1fr); gap:12px; margin-top:12px; align-items:stretch; }
.dispatch-workspace>.panel { margin-bottom:0; }
.dispatch-workspace>.panel,.dispatch-history-panel { overflow:hidden; border:1px solid var(--theme-border-color); border-radius:11px; box-shadow:var(--theme-shadow); animation:surface-in .44s cubic-bezier(.2,.75,.25,1) both; }
.dispatch-files-panel { animation-delay:.05s!important; }
.dispatch-confirm-panel { animation-delay:.1s!important; }
.panel-title { min-height:48px; padding:0 16px; border-bottom:1px solid var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.panel-title>div { gap:7px; }
.panel-title>div>small { color:var(--text-color-3); font-size:var(--font-auxiliary); font-weight:700; }
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
.file-download-cell { width:100px; margin-inline:auto; }
.file-download-cell button { min-height:29px; padding:4px 8px; }
.filename-standard { color:rgb(var(--success-color)); background:rgb(var(--success-color) / .1); }
.filename-standard.warning { color:rgb(var(--warning-color)); background:rgb(var(--warning-color) / .1); }
.dispatch-pagination { padding:9px 13px 0; }
.dispatch-download-hint { margin:11px 14px 13px; font-size:var(--font-auxiliary); line-height:1.55; }
.dispatch-confirm-panel { min-width:0; grid-column:1/-1; align-self:start; }
.dispatch-confirm-content { display:grid; grid-template-columns:140px minmax(240px,1fr) minmax(230px,.8fr) 210px; align-items:center; gap:16px; height:auto; padding:16px; box-sizing:border-box; }
.confirm-label { color:var(--text-color-3); font-size:var(--font-auxiliary); font-weight:700; }
.confirm-label { align-self:end; }
.confirm-total { grid-column:1; margin:0; color:var(--text-color-1); font-size:12px; }
.confirm-total strong { margin-right:3px; color:var(--text-color-1); font-size:25px; font-variant-numeric:tabular-nums; }
.confirm-file-list { grid-column:2; grid-row:1/3; min-height:86px; display:flex; flex-direction:column; gap:7px; padding:10px 12px; border:1px solid var(--theme-border-color); border-radius:7px; background:rgb(var(--container-bg-color)); }
.confirm-file-list>span { overflow:hidden; color:var(--text-color-2); text-overflow:ellipsis; white-space:nowrap; font-size:var(--font-auxiliary); }
.confirm-file-list>button { align-self:flex-end; min-height:22px; margin-top:auto; padding:0; }
.confirm-file-list.empty { align-items:center; justify-content:center; gap:8px; color:var(--text-color-3); text-align:center; }
.confirm-file-list.empty>span { color:var(--text-color-3); white-space:normal; line-height:1.5; }
.confirm-machine { grid-column:3; grid-row:1/3; margin:0; padding:0; border:0; }
.confirm-machine label { display:flex; align-items:center; gap:6px; margin-bottom:8px; color:var(--text-color-1); font-size:var(--font-auxiliary); font-weight:700; }
.confirm-machine .app-select { width:100%; }
.confirm-machine small { display:flex; align-items:flex-start; gap:6px; margin-top:8px; color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.45; }
.confirm-machine small i { margin-top:3px; }
.confirm-dispatch-button { min-height:40px; display:flex; align-items:center; justify-content:center; grid-column:4; grid-row:1/3; gap:7px; margin:0; padding:0 12px; }
.confirm-dispatch-button:hover:not(:disabled) { transform:translateY(-1px); }
.confirm-dispatch-button:disabled { cursor:not-allowed; }
.dispatch-history-panel { --content-table-font-size:13px; --content-table-header-font-size:12px; margin-top:24px; }
.history-table th { color:var(--theme-on-primary-color); background:var(--theme-table-header-color); }
.history-table .dispatch-project-name { font-size:12px; }
.dispatch-empty-history i { color:rgb(var(--info-color)); border:0; background:rgb(var(--info-color) / .1); }
.push-batch-backdrop { backdrop-filter:blur(5px); }
.push-batch-dialog { border:1px solid var(--theme-border-color); border-radius:14px; box-shadow:var(--theme-popover-shadow); }
.push-batch-header { color:var(--text-color-1); background:var(--theme-table-header-color); }
.push-batch-summary,.push-batch-error,.push-batch-progress { border-radius:7px; }
.push-batch-table-wrap { border-radius:8px; }

@media (max-width:1120px) {
  .dispatch-confirm-content { grid-template-columns:120px minmax(220px,1fr) minmax(210px,.8fr) 190px; }
}
@media (max-width:1000px) {
  .dispatch-hero { grid-template-columns:1fr 260px; padding:24px; }
  .dispatch-flow { grid-template-columns:repeat(2,1fr); }
  .dispatch-flow>div:nth-child(2) { border-right:0; }
  .dispatch-workspace.download-workspace { grid-template-columns:1fr; }
  .download-workspace .dispatch-files-panel { min-height:0; }
  .directory-panel { display:flex; flex-direction:column; }
  .dispatch-confirm-panel { grid-column:auto; }
  .dispatch-confirm-content { grid-template-columns:120px minmax(220px,1fr) minmax(220px,1fr); }
  .confirm-dispatch-button { grid-column:1/-1; grid-row:auto; margin-top:0; }
  .directory-scroll { position:static; min-height:220px; max-height:410px; flex:0 1 auto; }
}
@media (max-width:680px) {
  .dispatch-hero { grid-template-columns:1fr; min-height:190px; padding:24px; background:linear-gradient(90deg,rgb(var(--container-bg-color) / 0.97),rgb(var(--container-bg-color) / 0.88)),var(--dispatch-hero-image) center/cover; gap:24px; }
  .dispatch-hero h1 { font-size:var(--font-page-title-mobile); }
  .dispatch-hero__copy>p { font-size:12px; }
  .dispatch-source-status { gap:8px; margin:12px 0 0; }
  .dispatch-flow { grid-template-columns:1fr; margin:16px 0 0; padding:16px; gap:8px; }
  .dispatch-flow>div { min-height:58px; padding:10px 18px; border-bottom:1px solid var(--theme-border-color); }
  .dispatch-flow>div::after { display:none; }
  .file-name-search { align-items:stretch; gap:8px; padding:16px; }
  .download-batch-toolbar { align-items:stretch; gap:8px; padding:12px 16px; }
  .dispatch-confirm-content { display:flex; height:auto; gap:16px; padding:16px; }
  .confirm-file-list { min-height:100px; }
  .confirm-machine { margin-top:13px; padding-top:12px; border-top:1px solid var(--theme-border-color); }
  .confirm-dispatch-button { margin-top:14px; }
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
  .dispatch-hero,.dispatch-workspace>.panel,.dispatch-history-panel { animation:none; }
}
.history-table :is(code,.dispatch-status,.history-actions button) { font-size:inherit; }
@media (max-width:560px) {
  .dispatch-history-panel > .dispatch-table-wrap { padding:12px; }
}
</style>
