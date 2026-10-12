<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CatalogTreeNode from '../components/CatalogTreeNode.vue'
import AppIcon from '../components/AppIcon.vue'
import heroImage from '../assets/home-laser-cutting.webp'
import { parseCuttingInstructionFilename } from '../utils/cuttingInstructionName.js'
import { matchesDispatchParameters } from '../utils/cuttingDispatchBatch.js'
import { formatChineseDateTime } from '../utils/dateDisplay.js'
import { useDialogFocus } from '../composables/useDialogFocus.js'
import {
  cancelCuttingDispatch,
  createCuttingDispatchBatch,
  executeCuttingDispatch,
  getCuttingDispatch,
  getCuttingStatus,
  listCuttingDispatches,
  listCuttingMachines,
  listCuttingProjects,
  listCuttingCatalogs,
  listCuttingFiles,
} from '../api/reports'

const pageActive = ref(true)
const route = useRoute()
const router = useRouter()
let querySequence = 0
let lifecycleSequence = 0
let lifecycleController = null
let directoryController = null
let historyController = null
let firstActivation = true

function beginLifecycleRequest() {
  lifecycleController?.abort()
  lifecycleController = new AbortController()
  return { sequence: ++lifecycleSequence, signal: lifecycleController.signal }
}

function beginDirectoryRequest() {
  directoryController?.abort()
  directoryController = new AbortController()
  return directoryController.signal
}
const dispatchForm = reactive({ machineIp: '' })
const systemStatus = ref(null)
const rows = ref([])
const histories = ref([])
const historyPage = ref(1)
const historyPageSize = 10
const historyTotal = ref(0)
const historyLoading = ref(false)
const historyError = ref('')
const historyPageCount = computed(() => Math.max(1, Math.ceil(historyTotal.value / historyPageSize)))
const machines = ref([])
const bulkQuantity = ref(1)
const pushBatch = reactive({
  visible: false,
  saving: false,
  resultsReady: false,
  items: [],
  completed: 0,
  total: 0,
  currentFilename: '',
  error: '',
  nonStandardConfirmed: false,
  overrideConfirmed: false,
})
const pushBatchDialog = ref(null)
const pushBatchOpen = computed(() => pushBatch.visible)
const selectedFiles = ref(new Map())
const loading = ref(false)
const operatingId = ref('')
const notice = ref('')
const noticeType = ref('info')
const page = ref(1)
const requestedPage = ref(1)
const pageSize = 20
const total = ref(0)
const projectOptions = ref([])
const projectKeyword = ref('')
const initialProjectId = Array.isArray(route.query.project_id)
  ? route.query.project_id[0] || ''
  : String(route.query.project_id || '')
const selectedProjectId = ref(initialProjectId)
const filteredProjectOptions = computed(() => {
  const keyword = projectKeyword.value.trim().toLowerCase()
  if (!keyword) return projectOptions.value
  return projectOptions.value.filter((project) => (
    `${project.project_name || ''} ${project.ship_no || ''}`.toLowerCase().includes(keyword)
  ))
})
const projectCountLabel = computed(() => (
  projectKeyword.value.trim()
    ? `${filteredProjectOptions.value.length} / ${projectOptions.value.length} 个支持项目`
    : `${projectOptions.value.length} 个支持项目`
))
const projects = computed(() => {
  const selected = projectOptions.value.find((project) => String(project.project_id) === selectedProjectId.value)
  return selected ? [selected] : []
})
const catalogTree = ref([])
const fileKeyword = ref('')
const appliedFileKeyword = ref('')
const selectedProject = ref(null)
const selectedCatalog = ref(null)
const projectExpanded = ref(false)
const expandedCatalogIds = ref(new Set())
const projectLoading = ref(false)
const catalogLoading = ref(false)

const invalidBatchCount = computed(() => pushBatch.items.filter((item) => batchItemError(item)).length)
const nonStandardBatchCount = computed(() => pushBatch.items.filter((item) => !item.parsed).length)
const overrideBatchCount = computed(() => pushBatch.items.filter((item) => hasNameMismatch(item)).length)
const batchStatusCounts = computed(() => {
  const counts = { sent: 0, duplicate: 0, failed: 0, pending: 0 }
  pushBatch.items.forEach((item) => {
    const key = Object.hasOwn(counts, item.status) ? item.status : 'pending'
    counts[key] += 1
  })
  return counts
})
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const selectedFileCount = computed(() => selectedFiles.value.size)
const selectedFileItems = computed(() => [...selectedFiles.value.values()])
const selectedMachine = computed(() => machines.value.find((machine) => machine.machine_ip === dispatchForm.machineIp))

function machineName(machineIp) {
  const names = {
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
const selectableRows = computed(() => rows.value.filter((row) => row.path_available && systemStatus.value?.can_push))
const allVisibleFilesSelected = computed(() => (
  selectableRows.value.length > 0
  && selectableRows.value.every((row) => selectedFiles.value.has(fileKey(row)))
))
const someVisibleFilesSelected = computed(() => (
  !allVisibleFilesSelected.value
  && selectableRows.value.some((row) => selectedFiles.value.has(fileKey(row)))
))
const selectedPath = computed(() => {
  if (!selectedProject.value) return '请选择项目和目录'
  const catalog = selectedCatalog.value
  return catalog ? `${selectedProject.value.project_name} / ${catalogPath(catalog)}` : selectedProject.value.project_name
})

function showNotice(message, type = 'info') {
  notice.value = message
  noticeType.value = type
}

function fileKey(file) {
  return JSON.stringify([file.document_id, file.version_id, file.normalized_path])
}

function alreadySelectedDocument(files, file) {
  return [...files.values()].some((selected) => String(selected.document_id) === String(file.document_id))
}

function clearSelectedFiles() {
  selectedFiles.value = new Map()
}

function rememberSelectedProject(projectId) {
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

function setFileSelected(file, selected) {
  const next = new Map(selectedFiles.value)
  const key = fileKey(file)
  if (selected && !next.has(key) && next.size >= 20) {
    showNotice('一次最多选择 20 个文件，请先取消部分文件。', 'error')
    return
  }
  if (selected && !next.has(key) && alreadySelectedDocument(next, file)) {
    showNotice('同一 PDM 文件只能选择一个版本，请先取消已选版本。', 'error')
    return
  }
  if (selected) next.set(key, file)
  else next.delete(key)
  selectedFiles.value = next
}

function toggleVisibleFiles(selected) {
  const next = new Map(selectedFiles.value)
  let skipped = 0
  for (const file of selectableRows.value) {
    const key = fileKey(file)
    if (!selected) {
      next.delete(key)
    } else if (!next.has(key) && next.size < 20) {
      if (alreadySelectedDocument(next, file)) skipped += 1
      else next.set(key, file)
    } else if (!next.has(key)) {
      skipped += 1
    }
  }
  selectedFiles.value = next
  if (skipped) showNotice(`另有 ${skipped} 项因已满 20 项或文件版本重复而未选中。`, 'error')
}

function openSinglePush(file) {
  if (pushBatch.saving) return
  selectedFiles.value = new Map([[fileKey(file), file]])
  openPushBatch()
}

async function loadHistory({ signal, page: targetPage = historyPage.value } = {}) {
  if (!pageActive.value || signal?.aborted) return
  historyController?.abort()
  const controller = new AbortController()
  historyController = controller
  const abort = () => controller.abort()
  signal?.addEventListener('abort', abort, { once: true })
  historyLoading.value = true
  historyError.value = ''
  try {
    let data = await listCuttingDispatches({ page: targetPage, pageSize: historyPageSize }, { signal: controller.signal })
    if (controller.signal.aborted || !pageActive.value || historyController !== controller) return
    const lastPage = Math.max(1, Math.ceil(data.total / historyPageSize))
    if (targetPage > lastPage) {
      targetPage = lastPage
      data = await listCuttingDispatches({ page: targetPage, pageSize: historyPageSize }, { signal: controller.signal })
    }
    if (controller.signal.aborted || !pageActive.value || historyController !== controller) return
    histories.value = data.items
    historyTotal.value = data.total
    historyPage.value = data.page
  } catch (error) {
    if (error.name !== 'AbortError' && !controller.signal.aborted && pageActive.value && historyController === controller) {
      historyError.value = error.message
    }
  } finally {
    signal?.removeEventListener('abort', abort)
    if (historyController === controller) {
      historyLoading.value = false
      historyController = null
    }
  }
}

async function changeHistoryPage(value) {
  if (historyLoading.value || operatingId.value || pushBatch.saving || value < 1 || value > historyPageCount.value) return
  await loadHistory({ page: value })
}

async function loadMachines({ signal } = {}) {
  if (!systemStatus.value?.magic_box?.configured) return
  try {
    const data = await listCuttingMachines({ signal })
    if (signal?.aborted || !pageActive.value) return
    machines.value = data.items || []
    if (!dispatchForm.machineIp && machines.value.length) {
      dispatchForm.machineIp = machines.value[0].machine_ip
    }
  } catch (error) {
    machines.value = (systemStatus.value?.magic_box?.machine_ips || []).map((machineIp) => ({
      machine_ip: machineIp,
      online: false,
      configured: true,
    }))
    if (!dispatchForm.machineIp && machines.value.length) dispatchForm.machineIp = machines.value[0].machine_ip
    showNotice(`机床在线状态读取失败：${error.message}。仍可选择 SYS_CONFIG 中配置的机床。`, 'error')
  }
}

function createPushBatchItem(file) {
  const filename = file.document_no || file.physical_file_name || ''
  const parsed = parseCuttingInstructionFilename(filename)
  return {
    referenceKey: fileKey(file),
    documentId: String(file.document_id),
    versionId: file.version_id || null,
    filePath: file.normalized_path,
    filename,
    version: file.version || file.physical_version_code?.trim() || file.physical_version_no || '--',
    parsed,
    material: parsed?.material || '',
    thickness: parsed?.thickness ?? '',
    quantity: 1,
    status: 'pending',
    error: '',
    dispatchId: '',
    retryable: false,
    statusChecked: false,
  }
}

function batchItemError(item) {
  if (!String(item.material || '').trim()) return '请填写材质'
  const thickness = Number(item.thickness)
  if (!Number.isFinite(thickness) || thickness <= 0 || thickness > 1000) return '板厚必须大于 0 且不超过 1000 mm'
  const quantity = Number(item.quantity)
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) return '加工数量必须是 1–10000 的整数'
  return ''
}

function hasNameMismatch(item) {
  if (!item.parsed) return false
  return String(item.material || '').trim().toLowerCase() !== item.parsed.material.toLowerCase()
    || Number(item.thickness) !== item.parsed.thickness
}

function openPushBatch() {
  if (!selectedFileCount.value) {
    showNotice('请先选择要推送的文件。', 'error')
    return
  }
  pushBatch.items = [...selectedFiles.value.values()].map(createPushBatchItem)
  pushBatch.completed = 0
  pushBatch.total = pushBatch.items.length
  pushBatch.currentFilename = ''
  pushBatch.error = ''
  pushBatch.nonStandardConfirmed = false
  pushBatch.overrideConfirmed = false
  pushBatch.resultsReady = false
  bulkQuantity.value = 1
  pushBatch.visible = true
}

function closePushBatch() {
  if (pushBatch.saving) return
  pushBatch.visible = false
}

useDialogFocus(pushBatchOpen, pushBatchDialog, closePushBatch)

function removePushBatchItem(item) {
  if (pushBatch.saving || pushBatch.resultsReady) return
  pushBatch.items = pushBatch.items.filter((candidate) => candidate.referenceKey !== item.referenceKey)
  const next = new Map(selectedFiles.value)
  next.delete(item.referenceKey)
  selectedFiles.value = next
  pushBatch.total = pushBatch.items.length
  if (!pushBatch.items.length) closePushBatch()
}

function applyBulkQuantity() {
  const quantity = Number(bulkQuantity.value)
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) {
    pushBatch.error = '统一数量必须是 1–10000 的整数。'
    return
  }
  pushBatch.items.forEach((item) => { item.quantity = quantity })
  if (nonStandardBatchCount.value) pushBatch.nonStandardConfirmed = false
  pushBatch.error = ''
}

function invalidateBatchConfirmation(item) {
  if (item.parsed) pushBatch.overrideConfirmed = false
  else pushBatch.nonStandardConfirmed = false
}

function batchRequestItem(item) {
  return {
    document_id: item.documentId,
    version_id: item.versionId,
    file_path: item.filePath,
    material: String(item.material).trim(),
    thickness: Number(item.thickness),
    quantity: Number(item.quantity),
    name_check_confirmed: item.parsed ? hasNameMismatch(item) && pushBatch.overrideConfirmed : pushBatch.nonStandardConfirmed,
  }
}

function batchStatusLabel(item) {
  return {
    pending: '待创建',
    pushing: '下载并推送中',
    sent: '已推送',
    duplicate: '已有未完成任务',
    failed: '失败',
  }[item.status] || '待创建'
}

function catalogPath(node) {
  const names = [node.title]
  const byId = new Map()
  const visit = (items) => items.forEach((item) => { byId.set(String(item.catalog_id), item); visit(item.children || []) })
  visit(catalogTree.value)
  let parent = byId.get(String(node.parent_id))
  const seen = new Set([String(node.catalog_id)])
  while (parent && !seen.has(String(parent.catalog_id))) {
    names.unshift(parent.title)
    seen.add(String(parent.catalog_id))
    parent = byId.get(String(parent.parent_id))
  }
  return names.join(' / ')
}

function expandCatalog(node) {
  const next = new Set(expandedCatalogIds.value)
  const key = String(node.catalog_id)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedCatalogIds.value = next
}

async function loadProjects() {
  const sequence = ++querySequence
  const signal = beginDirectoryRequest()
  projectLoading.value = true
  let projectToOpen = null
  try {
    const data = await listCuttingProjects('', 1000, { signal })
    if (signal.aborted || !pageActive.value || sequence !== querySequence) return
    projectOptions.value = data.items || []
    if (!projectOptions.value.some((project) => String(project.project_id) === selectedProjectId.value)) {
      selectedProjectId.value = projectOptions.value[0] ? String(projectOptions.value[0].project_id) : ''
    }
    rememberSelectedProject(selectedProjectId.value)
    projectToOpen = projectOptions.value.find(
      (project) => String(project.project_id) === selectedProjectId.value,
    ) || null
    selectedProject.value = null
    selectedCatalog.value = null
    projectExpanded.value = false
    catalogTree.value = []
    expandedCatalogIds.value = new Set()
    rows.value = []
    clearSelectedFiles()
    total.value = 0
    page.value = 1
    notice.value = ''
  } catch (error) {
    if (error.name !== 'AbortError' && sequence === querySequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) projectLoading.value = false
  }
  if (projectToOpen && pageActive.value && !signal.aborted && sequence === querySequence) await selectProject(projectToOpen)
}

function resetProjectSelection() {
  rememberSelectedProject(selectedProjectId.value)
  querySequence += 1
  directoryController?.abort()
  directoryController = null
  catalogLoading.value = false
  loading.value = false
  selectedProject.value = null
  selectedCatalog.value = null
  projectExpanded.value = false
  catalogTree.value = []
  expandedCatalogIds.value = new Set()
  rows.value = []
  clearSelectedFiles()
  total.value = 0
  page.value = 1
  notice.value = ''
}

function applyProjectKeyword() {
  const options = filteredProjectOptions.value
  if (!options.some((project) => String(project.project_id) === selectedProjectId.value)) {
    selectedProjectId.value = options[0] ? String(options[0].project_id) : ''
  }
  resetProjectSelection()
}

async function openSelectedProject() {
  const project = projects.value[0]
  if (project) await selectProject(project)
}

async function selectProject(project) {
  if (selectedProject.value?.project_id === project.project_id && catalogTree.value.length) {
    projectExpanded.value = true
    return
  }
  const sequence = ++querySequence
  const signal = beginDirectoryRequest()
  selectedProjectId.value = String(project.project_id)
  rememberSelectedProject(selectedProjectId.value)
  selectedProject.value = project
  projectExpanded.value = true
  selectedCatalog.value = null
  catalogTree.value = []
  expandedCatalogIds.value = new Set()
  rows.value = []
  clearSelectedFiles()
  total.value = 0
  page.value = 1
  catalogLoading.value = true
  try {
    const data = await listCuttingCatalogs(project.project_id, { signal })
    if (signal.aborted || !pageActive.value || sequence !== querySequence) return
    let roots = data.tree || []
    // PDM may repeat the project itself as a catalog root. The project is
    // already the first visible node, so begin with its real child folders.
    if (roots.length === 1 && roots[0].children?.length
        && String(roots[0].title || '').trim() === String(project.project_name || '').trim()) {
      roots = roots[0].children
    }
    catalogTree.value = roots
    expandedCatalogIds.value = new Set(roots.map((item) => String(item.catalog_id)))
    notice.value = ''
  } catch (error) {
    if (error.name !== 'AbortError' && sequence === querySequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) catalogLoading.value = false
  }
}

function toggleProject(project) {
  if (selectedProject.value?.project_id !== project.project_id || !catalogTree.value.length) {
    selectProject(project)
    return
  }
  projectExpanded.value = !projectExpanded.value
}

async function selectCatalog(node) {
  selectedCatalog.value = node
  if (node.children?.length && !expandedCatalogIds.value.has(String(node.catalog_id))) expandCatalog(node)
  fileKeyword.value = ''
  appliedFileKeyword.value = ''
  clearSelectedFiles()
  await loadFiles(1)
}

async function applyFileSearch() {
  if (fileKeyword.value.trim() !== appliedFileKeyword.value) clearSelectedFiles()
  await loadFiles(1)
}

async function loadFiles(targetPage = 1) {
  if (!selectedProject.value || !selectedCatalog.value) return
  const sequence = ++querySequence
  const signal = beginDirectoryRequest()
  loading.value = true
  requestedPage.value = targetPage
  const keyword = fileKeyword.value.trim()
  try {
    const data = await listCuttingFiles({
      projectId: selectedProject.value.project_id,
      catalogId: selectedCatalog.value.catalog_id,
      keyword,
      page: targetPage,
      pageSize,
    }, { signal })
    if (signal.aborted || !pageActive.value || sequence !== querySequence) return
    rows.value = data.items
    total.value = data.total
    page.value = targetPage
    appliedFileKeyword.value = keyword
    notice.value = ''
  } catch (error) {
    if (error.name !== 'AbortError' && sequence === querySequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

async function changePage(nextPage) {
  if (loading.value || nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
  await loadFiles(nextPage)
}

function applyCreationResult(item, result) {
  item.dispatchId = result?.dispatch?.dispatch_id || ''
  item.retryable = false
  item.statusChecked = false
  if (result?.status === 'created' && item.dispatchId) {
    item.status = 'pending'
    item.error = ''
    return true
  }
  item.status = result?.status === 'duplicate' ? 'duplicate' : 'failed'
  item.error = result?.error || '任务创建结果不完整，请核对派发记录后重试'
  return false
}

function isStaleFileError(item) {
  return /版本已变化|路径已变化|PDM中未找到/.test(item.error)
}

async function syncBatchItem(item) {
  const current = await getCuttingDispatch(item.dispatchId)
  const matching = matchesDispatchParameters(current, item, dispatchForm.machineIp)
  item.statusChecked = true
  item.retryable = Boolean(current.can_execute) && matching
  if (!matching) {
    item.error = '已有任务的文件版本、机床或加工参数不一致，请在派发记录中核对，不要直接继续'
  } else if (current.status === 'sent') {
    item.status = 'sent'
    item.error = ''
  } else if (!current.can_execute) {
    item.error = `${current.last_error || current.status_label}；${current.stage_label}，请核对派发记录和魔盒状态`
  } else if (item.status === 'duplicate') {
    item.error = '已有同参数任务，可以核对后继续执行'
  } else {
    item.status = 'failed'
    item.error = current.last_error || '当前任务可安全重试'
  }
  return current
}

async function executeBatchItem(item) {
  item.status = 'pushing'
  item.retryable = false
  item.error = ''
  pushBatch.currentFilename = item.filename
  operatingId.value = item.dispatchId
  try {
    const completed = await executeCuttingDispatch(item.dispatchId)
    item.status = 'sent'
    if (completed.local_file_cleanup?.error) item.error = completed.local_file_cleanup.error
  } catch (error) {
    item.status = 'failed'
    item.error = error.message
    try {
      await syncBatchItem(item)
    } catch {
      item.error = `${error.message}；任务状态未知，请先核对状态，不要重复上传`
    }
  } finally {
    operatingId.value = ''
  }
}

async function refreshBatchHistory() {
  await Promise.allSettled([loadHistory()])
  const counts = batchStatusCounts.value
  const cleanupWarnings = pushBatch.items.filter((item) => item.status === 'sent' && item.error).length
  showNotice(
    `文件派发结果：成功 ${counts.sent} 项，失败 ${counts.failed} 项，重复 ${counts.duplicate} 项${cleanupWarnings ? `，本地文件清理警告 ${cleanupWarnings} 项` : ''}。`,
    counts.failed || counts.duplicate || cleanupWarnings ? 'error' : 'info',
  )
}

async function submitPushBatch() {
  if (pushBatch.saving) return
  pushBatch.error = ''
  if (!dispatchForm.machineIp) {
    pushBatch.error = '请选择目标机床。'
    return
  }
  if (!pushBatch.items.length) {
    pushBatch.error = '请至少保留一个文件。'
    return
  }
  if (invalidBatchCount.value) {
    pushBatch.error = `还有 ${invalidBatchCount.value} 个文件的加工参数不完整，请先补充。`
    return
  }
  if (nonStandardBatchCount.value && !pushBatch.nonStandardConfirmed) {
    pushBatch.error = `请先确认已人工核对 ${nonStandardBatchCount.value} 个非标准文件名及其加工参数。`
    return
  }
  if (overrideBatchCount.value && !pushBatch.overrideConfirmed) {
    pushBatch.error = `有 ${overrideBatchCount.value} 个文件的参数与标准文件名不一致，请先人工核对并确认。`
    return
  }
  if (!systemStatus.value?.can_push) {
    pushBatch.error = '当前文件来源或推送服务不可用。'
    return
  }

  pushBatch.saving = true
  pushBatch.resultsReady = false
  pushBatch.completed = 0
  pushBatch.total = pushBatch.items.length
  pushBatch.currentFilename = '正在创建独立派发任务'
  pushBatch.items.forEach((item) => {
    item.status = 'pending'
    item.error = ''
    item.dispatchId = ''
    item.retryable = false
    item.statusChecked = false
  })

  try {
    const created = await createCuttingDispatchBatch({
      machine_ip: dispatchForm.machineIp,
      items: pushBatch.items.map(batchRequestItem),
    })
    const createdItems = []
    for (const [index, item] of pushBatch.items.entries()) {
      const result = created.items?.[index]
      if (result && String(result.document_id) !== item.documentId) {
        item.status = 'failed'
        item.error = '任务创建结果与所选文件不一致，请核对派发记录'
        continue
      }
      if (applyCreationResult(item, result)) createdItems.push(item)
    }

    pushBatch.completed = pushBatch.items.length - createdItems.length
    for (const item of createdItems) {
      await executeBatchItem(item)
      pushBatch.completed += 1
    }

    pushBatch.resultsReady = true
    pushBatch.currentFilename = ''
    clearSelectedFiles()
    await refreshBatchHistory()
  } catch (error) {
    pushBatch.error = `任务创建结果未确认：${error.message}。重新提交时已创建的活动任务会显示为重复，请先核对其状态。`
  } finally {
    pushBatch.saving = false
    operatingId.value = ''
  }
}

async function retryBatchItem(item) {
  if (pushBatch.saving || !['failed', 'duplicate'].includes(item.status) || isStaleFileError(item)) return
  pushBatch.saving = true
  pushBatch.error = ''
  pushBatch.completed = 0
  pushBatch.total = 1
  pushBatch.currentFilename = item.filename
  try {
    if (item.dispatchId) {
      const current = await syncBatchItem(item)
      if (current.status === 'sent' || !item.retryable) return
    } else {
      const created = await createCuttingDispatchBatch({
        machine_ip: dispatchForm.machineIp,
        items: [batchRequestItem(item)],
      })
      if (!applyCreationResult(item, created.items?.[0])) return
    }
    await executeBatchItem(item)
  } catch (error) {
    item.status = 'failed'
    item.retryable = false
    item.error = `${error.message}；请核对任务状态后再重试`
  } finally {
    pushBatch.completed = 1
    pushBatch.currentFilename = ''
    pushBatch.saving = false
    await refreshBatchHistory()
  }
}

async function checkBatchItem(item) {
  if (pushBatch.saving || !item.dispatchId) return
  pushBatch.saving = true
  try {
    await syncBatchItem(item)
  } catch (error) {
    item.retryable = false
    item.error = `任务状态核对失败：${error.message}`
  } finally {
    pushBatch.saving = false
    await Promise.allSettled([loadHistory()])
  }
}

async function refreshBatchCatalog() {
  if (pushBatch.saving) return
  closePushBatch()
  clearSelectedFiles()
  await loadFiles(1)
}

async function executeSaved(item) {
  if (operatingId.value) return
  if (!window.confirm(`确认执行任务 ${item.dispatch_id}？\n文件：${item.document_no}\n机床：${machineName(item.machine_ip)}`)) return
  operatingId.value = item.dispatch_id
  try {
    const completed = await executeCuttingDispatch(item.dispatch_id)
    if (completed.local_file_cleanup?.error) {
      showNotice(`推送完成，但本地文件清理失败：${completed.local_file_cleanup.error}`, 'error')
    } else {
      showNotice(`推送完成，已删除系统中的文件副本。魔盒任务 UUID：${completed.remote_task_id}`)
    }
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    operatingId.value = ''
    await Promise.allSettled([loadHistory()])
  }
}

async function cancelSaved(item) {
  if (operatingId.value || !window.confirm(`确认取消任务 ${item.dispatch_id} 的机床指派？`)) return
  operatingId.value = item.dispatch_id
  try {
    await cancelCuttingDispatch(item.dispatch_id)
    showNotice('已取消魔盒机床指派。')
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    operatingId.value = ''
    await loadHistory()
  }
}

function formatTime(value) { return formatChineseDateTime(value) }

function fileExtension(row) {
  const source = String(row.document_no || row.physical_file_name || row.normalized_path || '')
  const filename = source.split(/[\\/]/).pop() || ''
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot).toUpperCase() : '--'
}

async function initializePage() {
  const { sequence, signal } = beginLifecycleRequest()
  try {
    systemStatus.value = await getCuttingStatus('push', { signal })
    if (signal.aborted || !pageActive.value || sequence !== lifecycleSequence) return
    await Promise.all([loadProjects(), loadMachines({ signal }), loadHistory({ signal })])
  } catch (error) {
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
  lifecycleSequence += 1
  querySequence += 1
  lifecycleController?.abort()
  lifecycleController = null
  directoryController?.abort()
  directoryController = null
  historyController?.abort()
  historyController = null
  historyLoading.value = false
  loading.value = false
  projectLoading.value = false
  catalogLoading.value = false
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
  const { sequence, signal } = beginLifecycleRequest()
  try {
    systemStatus.value = await getCuttingStatus('push', { signal })
    if (signal.aborted || !pageActive.value || sequence !== lifecycleSequence) return
    await Promise.all([loadMachines({ signal }), loadHistory({ signal })])
  } catch (error) {
    if (error.name !== 'AbortError' && sequence === lifecycleSequence) showNotice(`刷新文件派发状态失败：${error.message}`, 'error')
  } finally {
    if (sequence === lifecycleSequence) lifecycleController = null
  }
})
onDeactivated(pausePage)
onBeforeUnmount(pausePage)
</script>

<template>
  <section class="page-hero dispatch-hero" :style="{ '--dispatch-hero-image': `url(${heroImage})` }">
    <div class="dispatch-hero__copy">
      <span class="eyebrow">CUTTING FILE DISPATCH</span>
      <h1>文件派发</h1>
      <p>从 PDM 选择切割文件，核对标准名称和加工参数后，直接下载并推送到目标机床。</p>
      <div class="dispatch-source-status" aria-label="文件派发服务状态">
        <span><i :class="{ ready: systemStatus?.can_push }"></i>{{ systemStatus?.pdm?.label || '正在检查数据源' }}</span>
        <span>{{ systemStatus?.file_source?.label || '文件服务检查中' }}</span>
        <span>{{ systemStatus?.safe_mode ? '安全联调模式' : '生产数据模式' }}</span>
      </div>
    </div>
    <p class="dispatch-hero__slogan"><strong>精准的数据</strong><span>驱动高效的生产</span></p>
  </section>

  <div class="dispatch-flow" aria-label="文件派发流程">
    <div v-for="step in [{ number: 1, title: '选择项目目录', hint: '项目 · 分段 · 文件分类' }, { number: 2, title: '选择文件', hint: '标准名 · 加工参数' }, { number: 3, title: '确认派发', hint: '核对清单 · 设置机床' }, { number: 4, title: '完成派发', hint: '推送至目标机床' }]" :key="step.number" :class="{ active: activeDispatchStep === step.number, complete: activeDispatchStep > step.number }">
      <i><AppIcon v-if="activeDispatchStep > step.number" name="check" size="17" /> <template v-else>{{ step.number }}</template></i><span><strong>{{ step.title }}</strong><small>{{ step.hint }}</small></span>
    </div>
  </div>
  <p class="dispatch-push-scope" role="note"><AppIcon name="check" size="17" /><span>一次最多选择 20 个 PDM 文件；标准文件名自动识别参数，非标准文件名需人工核对。系统按顺序派发，单个文件失败不会中断其余任务。</span></p>

  <div v-if="notice" class="dispatch-notice" :class="{ error: noticeType === 'error' }" role="status">
    <span>{{ notice }}</span>
    <button type="button" @click="notice = ''">关闭</button>
  </div>

  <div class="dispatch-workspace download-workspace">
    <aside class="panel directory-panel">
      <div class="panel-title">
        <div><span class="dispatch-section-icon"><AppIcon name="briefcase" size="17" /></span><small>01</small><h2>项目目录</h2></div>
        <p>{{ projectCountLabel }}</p>
      </div>
      <form class="project-search" @submit.prevent="openSelectedProject">
        <label for="project-keyword">项目关键词 <small>项目名称或船号</small></label>
        <input id="project-keyword" v-model="projectKeyword" name="project_keyword" type="search" placeholder="输入关键词筛选…" autocomplete="off" :disabled="projectLoading" @input="applyProjectKeyword" />
        <label for="project-selector">支持项目</label>
        <div class="project-select-row"><AppSelect id="project-selector" v-model="selectedProjectId" :disabled="projectLoading || !filteredProjectOptions.length" @change="resetProjectSelection"><option v-if="projectLoading" value="" disabled>正在读取项目…</option><option v-else-if="!filteredProjectOptions.length" value="" disabled>没有匹配的支持项目</option><option v-for="project in filteredProjectOptions" :key="project.project_id" :value="String(project.project_id)">{{ project.project_name }}{{ project.ship_no ? ` · ${project.ship_no}` : '' }}</option></AppSelect><button type="submit" :disabled="projectLoading || catalogLoading || !selectedProjectId">{{ catalogLoading ? '打开中…' : '打开' }}</button></div>
      </form>
      <div class="directory-scroll" :aria-busy="projectLoading || catalogLoading">
        <div v-if="projectLoading" class="directory-message">正在读取项目…</div>
        <ul v-else-if="projects.length" class="project-tree" role="tree" aria-label="PDM项目目录">
          <li v-for="project in projects" :key="project.project_id" class="project-node" role="treeitem" :aria-expanded="selectedProject?.project_id === project.project_id && projectExpanded">
            <div class="project-node-row" :class="{ selected: selectedProject?.project_id === project.project_id && !selectedCatalog }">
              <button class="project-toggle" type="button" :aria-label="`${selectedProject?.project_id === project.project_id && projectExpanded ? '收起' : '展开'}${project.project_name}`" @click="toggleProject(project)"><span :class="{ expanded: selectedProject?.project_id === project.project_id && projectExpanded }">›</span></button>
              <span class="project-folder" aria-hidden="true"></span>
              <button class="project-label" type="button" @click="selectProject(project)"><strong>{{ project.project_name }}</strong><small>{{ project.ship_no || '未标注船号' }}</small></button>
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
        <div><input id="file-keyword" v-model.trim="fileKeyword" name="file_keyword" autocomplete="off" :disabled="!selectedCatalog" placeholder="输入文件名或中间产品…" /><button type="submit" :disabled="!selectedCatalog || loading">{{ loading ? '查询中…' : '查询' }}</button></div>
      </form>
      <div class="download-batch-toolbar" aria-live="polite">
        <span>已选择 <strong>{{ selectedFileCount }}</strong> / 20 项<small>可跨页选择</small></span>
        <div><button type="button" :disabled="!selectedFileCount || pushBatch.saving" @click="clearSelectedFiles">清空选择</button><button class="primary" type="button" :disabled="!selectedFileCount || loading || pushBatch.saving || !systemStatus?.can_push" @click="openPushBatch">设置参数并派发</button></div>
      </div>
      <div class="dispatch-table-wrap" :aria-busy="loading">
        <div v-if="loading" class="dispatch-loading" role="status">正在加载第 {{ requestedPage }} 页…</div>
        <table v-content-table="pageActive" class="dispatch-table source-file-table" aria-label="文件查询结果">
          <thead>
            <tr><th class="download-check-cell"><input type="checkbox" :checked="allVisibleFilesSelected" :indeterminate="someVisibleFilesSelected" :disabled="!selectableRows.length || loading || pushBatch.saving" aria-label="全选当前页可派发文件" @change="toggleVisibleFiles($event.target.checked)" /></th><th>文件名</th><th>命名检查</th><th>中间产品</th><th>文件格式</th><th>版本</th><th>操作人</th><th>操作人工号</th><th>操作时间</th><th class="file-download-cell">操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="fileKey(row)" :class="{ selected: selectedFiles.has(fileKey(row)) }">
              <td class="download-check-cell"><input type="checkbox" :checked="selectedFiles.has(fileKey(row))" :disabled="loading || pushBatch.saving || !row.path_available || !systemStatus?.can_push" :aria-label="`选择文件 ${row.document_no}`" @change="setFileSelected(row, $event.target.checked)" /></td>
              <td class="file-name-cell"><strong>{{ row.document_no }}</strong></td>
              <td><span class="filename-standard" :class="{ warning: !parseCuttingInstructionFilename(row.document_no) }">{{ parseCuttingInstructionFilename(row.document_no) ? '标准名' : '非标准名' }}</span></td>
              <td>{{ row.pbs_info || '--' }}</td>
              <td class="file-extension">{{ fileExtension(row) }}</td>
              <td>{{ row.version || row.physical_version_code?.trim() || row.physical_version_no || '--' }}</td>
              <td>{{ row.operator || row.physical_file_operator || '--' }}</td>
              <td>{{ row.operator_job_no || row.physical_file_operator_job_no || '--' }}</td>
              <td>{{ formatTime(row.operate_time || row.physical_file_date) }}</td>
              <td class="file-download-cell"><button type="button" :disabled="loading || pushBatch.saving || !row.path_available || !systemStatus?.can_push" :aria-label="`派发 ${row.document_no}`" @click="openSinglePush(row)">设置并派发</button><small v-if="!row.path_available">{{ row.path_error || '路径不可用' }}</small></td>
            </tr>
            <tr v-if="!loading && rows.length === 0"><td colspan="10" class="dispatch-empty-row">{{ selectedCatalog ? appliedFileKeyword ? '当前目录没有匹配的文件' : '当前目录没有文件' : '请从左侧选择一个目录' }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="dispatch-pagination app-pagination"><div class="app-pagination__actions"><button type="button" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button><span class="app-pagination__summary">第 {{ page }} / {{ totalPages }} 页，共 {{ total }} 条</span><button type="button" :disabled="page >= totalPages || loading" @click="changePage(page + 1)">下一页</button></div></div>
      <p class="dispatch-download-hint">确认后系统会复核当前 PDM 版本和路径，再临时下载、校验并推送；成功后删除本地副本。{{ systemStatus && !systemStatus.can_push ? '当前文件来源或魔盒服务不可用，暂不可派发。' : '' }}</p>
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
            <span v-for="file in selectedFileItems.slice(0, 5)" :key="fileKey(file)" :title="file.document_no || file.physical_file_name">{{ file.document_no || file.physical_file_name }}</span>
            <button v-if="selectedFileCount > 5" type="button" @click="openPushBatch">其余 {{ selectedFileCount - 5 }} 项 · 展开全部</button>
          </template>
          <template v-else><AppIcon name="file" size="23" /><span>请从目录文件中选择待派发文件</span></template>
        </div>
        <div class="confirm-machine">
          <label for="dispatch-machine"><AppIcon name="settings" size="16" />目标机床</label>
          <AppSelect id="dispatch-machine" v-model="dispatchForm.machineIp" :disabled="!machines.length || pushBatch.saving"><option value="" disabled>请选择目标机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machineName(machine.machine_ip) }}（{{ machine.online ? '在线' : '待确认' }}）</option></AppSelect>
          <small><i :class="{ ready: selectedMachine?.online }"></i>{{ selectedMachine ? `${machineName(selectedMachine.machine_ip)} · ${selectedMachine.online ? '机床在线' : '未从列表确认在线'}` : systemStatus?.file_source?.message || '请选择接收文件的机床' }}</small>
        </div>
        <button class="confirm-dispatch-button" type="button" :disabled="!selectedFileCount || loading || pushBatch.saving || !systemStatus?.can_push || !dispatchForm.machineIp" @click="openPushBatch"><AppIcon name="send" size="18" tone="light" />设置参数并派发</button>
      </div>
    </aside>
  </div>

  <section class="panel dispatch-history-panel" :aria-busy="historyLoading">
    <div class="panel-title"><div><span class="dispatch-section-icon green"><AppIcon name="check" size="17" /></span><small>04</small><h2>派发记录</h2></div><p>文件准备 → 魔盒上传 → 机床指派，各阶段独立记录</p></div>
    <div v-if="historyError" class="global-error" role="alert"><span>派发记录读取失败：{{ historyError }}</span><button type="button" :disabled="historyLoading" @click="loadHistory()">重试</button></div>
    <div v-if="historyLoading && !histories.length" class="dispatch-empty-history" role="status"><span class="spinner dark" aria-hidden="true"></span><strong>正在读取派发记录</strong></div>
    <div v-else-if="histories.length" class="dispatch-table-wrap"><table v-content-table="pageActive" class="dispatch-table history-table"><thead><tr><th>任务标识</th><th>文件名</th><th>目标机床</th><th>阶段 / 状态</th><th>魔盒UUID</th><th>失败原因</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in histories" :key="item.dispatch_id"><td><code>{{ item.task_identifier || item.dispatch_id }}</code></td><td>{{ item.document_no }}<small class="dispatch-project-name">数量 {{ item.quantity }}</small></td><td>{{ machineName(item.machine_ip) }}</td><td><span class="dispatch-status" :class="item.status">{{ item.status_label }}</span><small class="dispatch-project-name">{{ item.stage_label }}</small></td><td><code>{{ item.remote_task_id || '--' }}</code></td><td class="history-error">{{ item.last_error || '--' }}</td><td>{{ formatTime(item.created_at) }}</td><td><div class="history-actions"><button v-if="item.can_execute" type="button" :disabled="!!operatingId || historyLoading" @click="executeSaved(item)">{{ item.remote_task_id ? '重试指派' : '执行推送' }}</button><button v-if="item.can_cancel" type="button" class="danger" :disabled="!!operatingId || historyLoading" @click="cancelSaved(item)">取消指派</button><span v-if="!item.can_execute && !item.can_cancel">--</span></div></td></tr></tbody></table></div>
    <div v-else-if="!historyError" class="dispatch-empty-history"><i><AppIcon name="send" size="20" /></i><strong>暂无派发记录</strong><span>从项目目录选择文件并确认后，即可直接派发。</span></div>
    <div v-if="historyTotal > 0" class="dispatch-pagination app-pagination" aria-label="派发记录分页">
      <div class="app-pagination__actions">
        <button type="button" :disabled="historyPage <= 1 || historyLoading || !!operatingId || pushBatch.saving" @click="changeHistoryPage(historyPage - 1)">上一页</button>
        <span class="app-pagination__summary">第 {{ historyPage }} / {{ historyPageCount }} 页，共 {{ historyTotal }} 条</span>
        <button type="button" :disabled="historyPage >= historyPageCount || historyLoading || !!operatingId || pushBatch.saving" @click="changeHistoryPage(historyPage + 1)">下一页</button>
      </div>
    </div>
  </section>

  <div v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar-spacer" aria-hidden="true"></div>
  <aside v-if="selectedFileCount && !pushBatch.visible" class="mobile-selection-bar" aria-label="已选文件操作">
    <span><small>已选</small><strong>{{ selectedFileCount }}</strong><small>/ 20 项</small></span>
    <button type="button" :disabled="pushBatch.saving" @click="clearSelectedFiles">清空</button>
    <button class="primary" type="button" :disabled="loading || pushBatch.saving || !systemStatus?.can_push" @click="openPushBatch"><AppIcon name="send" size="16" tone="light" />设置参数并派发</button>
  </aside>

  <Teleport to="body">
    <div v-if="pushBatch.visible" class="push-batch-backdrop" role="presentation" @click.self="closePushBatch">
      <section ref="pushBatchDialog" class="push-batch-dialog" role="dialog" aria-modal="true" aria-labelledby="push-batch-title" tabindex="-1">
        <header class="push-batch-header">
          <div><span>FILE DISPATCH</span><h2 id="push-batch-title">{{ pushBatch.items.length === 1 ? '文件派发确认' : '批量派发确认' }}</h2><p>每个文件会生成独立任务，一项失败不影响后续文件。</p></div>
          <button type="button" :disabled="pushBatch.saving" aria-label="关闭批量推送窗口" @click="closePushBatch">×</button>
        </header>

        <div class="push-batch-content">
          <div class="push-batch-common">
            <label><span>目标机床</span><AppSelect v-model="dispatchForm.machineIp" :disabled="pushBatch.saving || pushBatch.resultsReady"><option value="" disabled>请选择目标机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machineName(machine.machine_ip) }}（{{ machine.online ? '在线' : '未从列表确认在线' }}）</option></AppSelect></label>
            <label><span>统一加工数量</span><div><input v-model="bulkQuantity" name="bulk_quantity" type="number" min="1" max="10000" step="1" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" /><button type="button" :disabled="pushBatch.saving || pushBatch.resultsReady" @click="applyBulkQuantity">应用到全部</button></div></label>
            <div class="push-batch-summary"><strong>{{ pushBatch.items.length }}</strong><span>个文件</span><small v-if="!pushBatch.resultsReady">{{ invalidBatchCount ? `${invalidBatchCount} 项待补参数` : nonStandardBatchCount || overrideBatchCount ? `${nonStandardBatchCount} 项非标准名、${overrideBatchCount} 项参数不符待确认` : '全部为标准名' }}</small><small v-else>已推送 {{ batchStatusCounts.sent }} · 失败 {{ batchStatusCounts.failed }} · 重复 {{ batchStatusCounts.duplicate }}</small></div>
          </div>

          <div v-if="pushBatch.error" class="push-batch-error" role="alert">{{ pushBatch.error }}</div>
          <div v-if="pushBatch.saving" class="push-batch-progress" role="status"><div><strong>{{ pushBatch.currentFilename }}</strong><span>{{ pushBatch.completed }} / {{ pushBatch.total }}</span></div><progress :value="pushBatch.completed" :max="pushBatch.total"></progress><small>正在按顺序处理，请不要关闭页面。</small></div>

          <div class="push-batch-table-wrap">
            <table v-content-table="pageActive" class="push-batch-table">
              <thead><tr><th>文件 / 识别结果</th><th>材质</th><th>板厚（mm）</th><th>数量</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="item in pushBatch.items" :key="item.referenceKey" :class="{ invalid: !pushBatch.resultsReady && (batchItemError(item) || !item.parsed || hasNameMismatch(item)) }">
                  <td class="push-batch-file"><strong>{{ item.filename }}</strong><small>版本 {{ item.version }}</small><span v-if="item.parsed" class="recognized">已识别：{{ item.parsed.material }} · {{ item.parsed.thickness.toFixed(1) }} mm</span><span v-else class="manual">非标准名，请手工填写参数</span><span v-if="hasNameMismatch(item)" class="manual">加工参数与标准名不一致，请人工复核</span></td>
                  <td><input v-model.trim="item.material" :name="`material_${item.referenceKey}`" :aria-label="`${item.filename} 材质`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" maxlength="80" placeholder="例如：Q235B…" @input="invalidateBatchConfirmation(item)" /></td>
                  <td><input v-model="item.thickness" :name="`thickness_${item.referenceKey}`" :aria-label="`${item.filename} 板厚`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" type="number" inputmode="decimal" min="0.01" max="1000" step="0.01" placeholder="例如：12.0…" @input="invalidateBatchConfirmation(item)" /></td>
                  <td><input v-model="item.quantity" :name="`quantity_${item.referenceKey}`" :aria-label="`${item.filename} 数量`" autocomplete="off" :disabled="pushBatch.saving || pushBatch.resultsReady" type="number" inputmode="numeric" min="1" max="10000" step="1" @input="invalidateBatchConfirmation(item)" /></td>
                  <td class="push-batch-result"><span :class="item.status">{{ batchStatusLabel(item) }}</span><small v-if="item.dispatchId" :title="item.dispatchId">任务 {{ item.dispatchId }}</small><small v-if="item.error" :title="item.error">{{ item.error }}</small></td>
                  <td class="push-batch-actions"><template v-if="pushBatch.resultsReady"><button v-if="item.status === 'failed' && isStaleFileError(item)" type="button" :disabled="pushBatch.saving" @click="refreshBatchCatalog">刷新清单重选</button><button v-else-if="item.status === 'failed' && (!item.dispatchId || item.retryable)" type="button" :disabled="pushBatch.saving" @click="retryBatchItem(item)">{{ item.dispatchId ? '重试当前任务' : '重试创建并派发' }}</button><button v-if="item.status === 'duplicate' && item.dispatchId && (!item.statusChecked || item.retryable)" type="button" :disabled="pushBatch.saving" @click="retryBatchItem(item)">核对并继续已有任务</button><button v-if="item.dispatchId && (item.status === 'failed' || item.status === 'duplicate')" type="button" :disabled="pushBatch.saving" @click="checkBatchItem(item)">核对状态</button><span v-if="item.status === 'sent'">已完成</span></template><button v-else class="push-batch-remove" type="button" :disabled="pushBatch.saving" @click="removePushBatchItem(item)">移除</button></td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-if="!pushBatch.resultsReady" class="push-batch-safety">
            <p>标准指令名已自动填充材质和板厚，请在提交前逐项核对。非标准名不会猜测参数，必须手工补全。</p>
            <label v-if="nonStandardBatchCount"><input v-model="pushBatch.nonStandardConfirmed" name="non_standard_confirmed" type="checkbox" :disabled="pushBatch.saving" />我已人工核对 {{ nonStandardBatchCount }} 个非标准文件名及其材质、板厚和数量</label>
            <label v-if="overrideBatchCount"><input v-model="pushBatch.overrideConfirmed" name="standard_override_confirmed" type="checkbox" :disabled="pushBatch.saving" />我已人工核对 {{ overrideBatchCount }} 个与标准名不一致的材质或板厚</label>
          </div>
        </div>

        <footer class="push-batch-footer">
          <template v-if="pushBatch.resultsReady"><span>可重试安全失败项；上传结果待核对的任务须先检查魔盒，系统不会自动重复上传。</span><button class="primary" type="button" :disabled="pushBatch.saving" @click="closePushBatch">完成</button></template>
          <template v-else><span>确认后将按顺序执行下载、校验、上传和机床指派。</span><div><button type="button" :disabled="pushBatch.saving" @click="closePushBatch">取消</button><button class="primary" type="button" :disabled="pushBatch.saving || invalidBatchCount > 0 || !dispatchForm.machineIp || !systemStatus?.can_push || (nonStandardBatchCount > 0 && !pushBatch.nonStandardConfirmed) || (overrideBatchCount > 0 && !pushBatch.overrideConfirmed)" @click="submitPushBatch">{{ pushBatch.saving ? '顺序派发中…' : pushBatch.items.length === 1 ? '确认派发' : '确认并顺序派发' }}</button></div></template>
        </footer>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.dispatch-project-name { display:block; color:var(--muted); font-size:10px; max-width:140px; overflow-wrap:anywhere; margin-top:5px; }
.dispatch-stage-badge { min-width:310px; display:flex; flex-direction:column; padding:17px 20px; border:1px solid #e2c88f; border-right:4px solid #c78a22; background:rgba(255,250,238,.9); box-shadow:0 12px 30px rgba(62,48,20,.06); }
.dispatch-stage-badge span { color:#a86b10; font-size:10px; font-weight:700; letter-spacing:2px; }
.dispatch-stage-badge strong { margin:4px 0; color:#5f4928; font-size:13px; }
.dispatch-stage-badge small { color:#89785d; }
.dispatch-flow { display:grid; grid-template-columns:repeat(2,1fr); margin-bottom:22px; border:1px solid var(--line); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.045); }
.dispatch-push-scope { padding:14px 18px; margin:0 0 22px; background:#fff6e4; border-left:4px solid #c78a22; color:#7b581d; font-size:12px; line-height:1.7; }
.dispatch-flow > div { position:relative; min-height:82px; display:flex; align-items:center; gap:12px; padding:17px 20px; border-right:1px solid var(--line); }
.dispatch-flow > div:last-child { border-right:0; }
.dispatch-flow > div::after { content:'›'; position:absolute; right:-7px; z-index:1; color:#b7c5cd; background:#fff; font-size:24px; }
.dispatch-flow > div:last-child::after { display:none; }
.dispatch-flow i { width:32px; height:32px; display:grid; place-items:center; flex:0 0 auto; color:#718895; border:1px solid #c6d1d8; border-radius:50%; font-style:normal; font-size:10px; }
.dispatch-flow span { display:flex; flex-direction:column; }
.dispatch-flow strong { font-size:12px; }
.dispatch-flow small { margin-top:3px; color:var(--muted); font-size:10px; }
.dispatch-flow .active { background:#f1f8fa; }
.dispatch-flow .active i { color:#fff; border-color:var(--blue); background:var(--blue); }
.dispatch-notice { display:flex; align-items:center; gap:12px; margin-bottom:22px; padding:13px 16px; color:#1f6570; border-left:4px solid var(--cyan); background:#edf8f9; font-size:12px; }
.dispatch-notice.error { color:#8a342d; border-left-color:#c95a50; background:#fff0ee; }
.dispatch-notice button { min-height:30px; margin-left:auto; padding:0 8px; color:var(--blue); border:0; background:transparent; }
.dispatch-workspace { display:grid; grid-template-columns:minmax(0,1.8fr) minmax(300px,.72fr); gap:22px; align-items:start; }
.dispatch-workspace.download-workspace { grid-template-columns:minmax(285px,.42fr) minmax(0,1.58fr); align-items:stretch; }
.dispatch-workspace.push-workspace { grid-template-columns:1fr; align-items:stretch; }
.dispatch-files-panel { min-width:0; }
.download-workspace .dispatch-files-panel { display:flex; min-height:600px; flex-direction:column; }
.download-workspace .dispatch-table-wrap { flex:1 1 auto; }
.directory-panel { min-width:0; }
.project-search,.file-name-search { padding:15px 18px; border-bottom:1px solid #e3eaee; background:#f8fafb; }
.project-search { padding:13px 14px; }
.project-search label,.file-name-search label { display:block; margin-bottom:6px; color:#506775; font-size:10px; font-weight:700; }
.project-search label:not(:first-child) { margin-top:10px; }
.project-search label small { margin-left:5px; color:#8a9aa5; font-size:9px; font-weight:400; }
.project-search > div,.file-name-search > div { display:flex; gap:7px; }
.project-search input,.project-search .app-select,.file-name-search input { width:100%; min-width:0; }
.project-search input,.project-search .app-select { height:36px; font-size:12px; }
.project-search .app-select__trigger { padding:0 10px; }
.project-search button,.file-name-search button { min-width:58px; padding:0 11px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; }
.project-search button { min-height:36px; height:36px; font-size:11px; }
.project-search button:disabled,.file-name-search button:disabled { opacity:.55; }
.directory-scroll { min-height:360px; max-height:610px; overflow:auto; padding:10px 9px 15px; }
.project-tree,.project-catalogs { margin:0; padding:0; list-style:none; }
.project-catalogs { margin-left:18px; border-left:1px solid #dbe5ea; }
.project-node-row { display:flex; align-items:center; min-height:40px; padding:3px 6px 3px 2px; border-radius:4px; }
.project-node-row:hover { background:#f0f6f8; }
.project-node-row.selected { background:#dff1f5; box-shadow:inset 3px 0 #1688a2; }
.project-toggle { width:25px; height:30px; display:grid; place-items:center; flex:0 0 25px; padding:0; border:0; color:#58707e; background:transparent; }
.project-toggle span { display:block; font-size:21px; line-height:1; transition:transform .15s ease; }
.project-toggle span.expanded { transform:rotate(90deg); }
.project-folder { position:relative; width:19px; height:13px; flex:0 0 19px; margin:0 8px 0 1px; border:1px solid #4d9bb7; border-radius:2px; background:#cfedf4; }
.project-folder::before { content:''; position:absolute; left:1px; top:-5px; width:9px; height:4px; border:1px solid #4d9bb7; border-bottom:0; border-radius:2px 2px 0 0; background:#cfedf4; }
.project-label { min-width:0; display:flex; align-items:flex-start; flex-direction:column; padding:6px 2px; border:0; background:transparent; text-align:left; cursor:pointer; }
.project-label strong { max-width:100%; overflow:hidden; color:#273f50; text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.project-label small { margin-top:2px; color:#7b8d98; font-size:9px; }
.directory-message { padding:18px 12px; color:var(--muted); text-align:center; font-size:11px; }
.selected-directory { display:flex; align-items:center; gap:10px; padding:12px 18px; border-bottom:1px solid #e4eaee; background:#f1f8fa; }
.selected-directory span { flex:0 0 auto; color:#718895; font-size:9px; font-weight:700; letter-spacing:1px; }
.selected-directory strong { min-width:0; overflow:hidden; color:#155d7b; text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
.file-name-search { display:flex; align-items:end; justify-content:space-between; gap:18px; }
.file-name-search > div { width:min(430px,100%); }
.file-name-search label { margin:0; flex:0 0 auto; }
.download-batch-toolbar { min-height:52px; display:flex; align-items:center; justify-content:space-between; gap:14px; padding:9px 18px; border-bottom:1px solid #e3eaee; background:#f7fafb; }
.download-batch-toolbar > span { color:var(--muted); font-size:10px; }
.download-batch-toolbar > span strong { margin:0 3px; color:var(--blue); font-size:15px; font-variant-numeric:tabular-nums; }
.download-batch-toolbar > span small { margin-left:10px; color:#a26917; }
.download-batch-toolbar > div { display:flex; gap:7px; }
.download-batch-toolbar button { min-height:34px; padding:0 12px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; white-space:nowrap; }
.download-batch-toolbar button.primary { color:#fff; border-color:var(--blue); background:var(--blue); }
.download-batch-toolbar button:disabled { opacity:.55; cursor:not-allowed; }
.dispatch-table-wrap { overflow-x:auto; position:relative; min-height:75px; }
.dispatch-loading { position:absolute; z-index:2; inset:0; display:flex; align-items:flex-start; justify-content:center; padding-top:48px; background:rgba(247,251,253,.8); color:var(--blue); font-size:12px; }
.file-download-cell { width:122px; }.file-download-cell button { min-height:32px; padding:6px 10px; border:1px solid #bdd2dd; background:#fff; color:var(--blue); font-size:11px; white-space:nowrap; cursor:pointer; }.file-download-cell button:disabled { opacity:.55; cursor:not-allowed; }.file-download-cell small { display:block; margin-top:4px; color:var(--muted); font-size:10px; }
.source-file-table .file-download-cell { position:sticky; z-index:1; right:0; background:#fff; box-shadow:-8px 0 12px -12px rgba(15,49,70,.65); }
.source-file-table thead .file-download-cell { z-index:2; background:#0b4f76; }
.source-file-table tbody tr:hover .file-download-cell { background:#f5fafb; }
.source-file-table tbody tr.selected .file-download-cell { background:#eef8fa; }
.dispatch-download-hint { margin:16px 22px; font-size:11px; color:var(--muted); }
.dispatch-table { width:100%; border-collapse:collapse; }
.dispatch-table th,.dispatch-table td { padding:10px 12px; border-bottom:1px solid #e4eaee; text-align:left; font-size:11px; }
.dispatch-table th { color:#dcecf2; background:#0b4f76; font-size:10px; letter-spacing:.4px; }
.dispatch-table tbody tr { cursor:default; }
.dispatch-table tbody tr:hover { background:#f5fafb; }
.dispatch-table tbody tr.selected { background:#eef8fa; box-shadow:inset 3px 0 var(--cyan); }
.dispatch-table input[type='checkbox'] { width:15px; height:15px; }
.download-check-cell { width:44px; text-align:center!important; }
.download-check-cell input { display:block; margin:auto; accent-color:var(--blue); cursor:pointer; }
.download-check-cell input:disabled { cursor:not-allowed; }
.check-column { width:42px; text-align:center!important; }
.file-name-cell { max-width:230px; }
.file-name-cell strong,.file-name-cell small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.file-name-cell strong { color:var(--navy); font-size:11px; }
.file-name-cell small { max-width:270px; margin-top:4px; color:#84939d; font:9px ui-monospace,SFMono-Regular,Menlo,monospace; }
.file-extension { color:#526a79; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.push-file-picker { min-width:0; display:flex; flex-direction:column; }
.push-file-filter { display:grid; grid-template-columns:minmax(180px,1.2fr) minmax(150px,.9fr) minmax(190px,1.1fr) auto; align-items:end; gap:10px; padding:16px 18px; border-bottom:1px solid #e3eaee; background:#f8fafb; }
.push-file-filter label > span { display:block; margin-bottom:6px; color:#425767; font-size:10px; font-weight:700; }
.push-file-filter input,.push-file-filter .app-select { width:100%; min-width:0; }
.push-filter-actions { display:flex; justify-content:flex-end; gap:7px; }
.push-filter-actions button { min-height:38px; padding:0 13px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; }
.push-filter-actions .primary { color:#fff; border-color:var(--blue); background:var(--blue); }
.push-file-table tbody tr { cursor:pointer; }
.push-check-cell { width:48px; text-align:center!important; }
.push-check-cell input { display:block; width:15px; height:15px; margin:auto; accent-color:var(--blue); cursor:pointer; }
.push-file-origin { max-width:245px; }
.push-file-origin strong,.push-file-origin small { display:block; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.push-file-origin strong { color:var(--navy); font-size:11px; }
.push-file-origin small { max-width:230px; margin-top:4px; color:var(--muted); font-size:9px; }
.push-file-picker .dispatch-table-wrap { min-height:210px; flex:1 1 auto; }
.dispatch-status { display:inline-flex; padding:4px 8px; color:#8b5d12; border-radius:999px; background:#fff3d8; white-space:nowrap; font-size:10px; }
.dispatch-status.sent { color:#267048; background:#e8f7ef; }
.dispatch-status.failed { color:#9b3f37; background:#fdecea; }
.dispatch-empty-row { padding:32px!important; color:var(--muted); text-align:center!important; }
.dispatch-pagination { display:flex; align-items:center; justify-content:flex-end; gap:12px; padding:14px 22px 0; color:var(--muted); font-size:10px; }
.dispatch-pagination button { min-height:30px; padding:0 12px; color:var(--blue); border:1px solid #c8d7df; background:#fff; }
.dispatch-pagination button:disabled { color:#9cabb4; background:#f4f6f7; cursor:not-allowed; }
.dispatch-empty-history { min-height:160px; display:flex; align-items:center; justify-content:center; flex-direction:column; padding:30px; color:var(--muted); text-align:center; }
.dispatch-empty-history i { width:38px; height:38px; display:grid; place-items:center; margin-bottom:10px; color:var(--blue); border:1px solid #a9cad7; border-radius:50%; background:#f1fafb; font-style:normal; font-size:18px; }
.dispatch-empty-history strong { color:#405866; font-size:13px; }
.dispatch-empty-history span { margin-top:5px; font-size:10px; }
.history-table { min-width:1180px; }
.dispatch-history-panel { margin-top:22px; }
.history-table code { color:var(--blue); font-size:9px; }
.history-error { max-width:220px; color:#8a4c45; overflow-wrap:anywhere; }
.history-actions { display:flex; align-items:center; gap:6px; }
.history-actions button { min-height:30px; padding:0 9px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; white-space:nowrap; font-size:10px; }
.history-actions button.danger { color:#9b3f37; border-color:#e3b8b4; }
.push-batch-backdrop { position:fixed; z-index:1000; inset:0; display:grid; place-items:center; padding:24px; background:rgba(13,39,55,.6); }
.push-batch-dialog { width:min(1180px,100%); max-height:calc(100vh - 48px); display:flex; flex-direction:column; overflow:hidden; color:#1d3444; background:#fff; box-shadow:0 24px 70px rgba(5,25,38,.28); }
.push-batch-header { display:flex; align-items:flex-start; justify-content:space-between; gap:24px; padding:20px 24px; color:#fff; background:#0b5678; }
.push-batch-header span { color:#a9e1eb; font-size:9px; font-weight:700; letter-spacing:2px; }
.push-batch-header h2 { margin:3px 0 4px; font-size:20px; }
.push-batch-header p { margin:0; color:#d6e9ef; font-size:10px; }
.push-batch-header button { width:34px; min-height:34px; flex:0 0 34px; padding:0; color:#fff; border:1px solid rgba(255,255,255,.4); background:transparent; font-size:23px; line-height:1; }
.push-batch-content { min-height:0; overflow:auto; padding:20px 24px; }
.push-batch-common { display:grid; grid-template-columns:minmax(280px,1.2fr) minmax(230px,.8fr) minmax(170px,.55fr); align-items:end; gap:16px; margin-bottom:18px; }
.push-batch-common label > span { display:block; margin-bottom:7px; color:#425767; font-size:10px; font-weight:700; }
.push-batch-common label > div { display:flex; gap:7px; }
.push-batch-common input,.push-batch-common .app-select { width:100%; min-width:0; }
.push-batch-common button { flex:0 0 auto; min-height:38px; padding:0 11px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; }
.push-batch-summary { min-height:61px; display:flex; align-items:baseline; align-content:center; flex-wrap:wrap; gap:4px; padding:10px 13px; border-left:3px solid var(--cyan); background:#eef8fa; }
.push-batch-summary strong { color:var(--blue); font-size:22px; }
.push-batch-summary span { font-size:11px; }
.push-batch-summary small { width:100%; color:#637b89; font-size:9px; }
.push-batch-error { margin-bottom:14px; padding:10px 13px; color:#943d35; border-left:3px solid #c95a50; background:#fff0ee; font-size:11px; }
.push-batch-progress { margin-bottom:14px; padding:11px 13px; background:#eef8fa; }
.push-batch-progress > div { display:flex; align-items:center; justify-content:space-between; gap:15px; }
.push-batch-progress strong { min-width:0; overflow:hidden; color:#155d7b; text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
.push-batch-progress span,.push-batch-progress small { color:#647b89; font-size:9px; }
.push-batch-progress progress { width:100%; height:7px; margin:8px 0 4px; accent-color:var(--cyan); }
.push-batch-table-wrap { overflow:auto; max-height:410px; border:1px solid #dce5ea; }
.push-batch-table { width:100%; min-width:920px; border-collapse:collapse; }
.push-batch-table th,.push-batch-table td { padding:10px; border-bottom:1px solid #e2e9ed; text-align:left; font-size:10px; vertical-align:middle; }
.push-batch-table th { position:sticky; z-index:1; top:0; color:#dcecf2; background:#0b4f76; font-size:9px; }
.push-batch-table tr.invalid { background:#fff9ef; }
.push-batch-table input { width:100%; min-width:82px; height:34px; padding:0 9px; }
.push-batch-file { width:290px; }
.push-batch-file strong,.push-batch-file small,.push-batch-file span { display:block; }
.push-batch-file strong { max-width:280px; overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:11px; }
.push-batch-file small { margin-top:3px; color:var(--muted); }
.push-batch-file span { margin-top:5px; font-size:9px; }
.push-batch-file .recognized { color:#167e85; }
.push-batch-file .manual { color:#a26917; }
.push-batch-result { width:170px; }
.push-batch-result > span { display:inline-flex; padding:4px 7px; color:#7b5d20; border-radius:999px; background:#fff3d8; white-space:nowrap; }
.push-batch-result > span.sent,.push-batch-result > span.saved { color:#267048; background:#e8f7ef; }
.push-batch-result > span.failed { color:#9b3f37; background:#fdecea; }
.push-batch-result > span.pushing { color:#155d7b; background:#e5f4f8; }
.push-batch-result > span.duplicate { color:#805a20; background:#f5ead7; }
.push-batch-result small { display:block; max-width:220px; margin-top:5px; color:#9b3f37; overflow-wrap:anywhere; }
.push-batch-remove { min-height:30px; padding:0 8px; color:#8b4c46; border:1px solid #dfc0bc; background:#fff; }
.push-batch-actions { min-width:140px; }
.push-batch-actions button { display:block; min-height:30px; margin:3px 0; padding:0 8px; color:var(--blue); border:1px solid #b9ccd6; border-radius:7px; background:#fff; }
.push-batch-actions button:disabled { opacity:.55; cursor:not-allowed; }
.push-batch-actions span { color:#267048; font-size:11px; }
.push-batch-safety { margin:14px 0 0; color:#7b581d; font-size:10px; line-height:1.7; }
.push-batch-safety p { margin:0; }
.push-batch-safety label { display:flex; align-items:center; gap:7px; margin-top:8px; color:#91483e; font-weight:700; }
.push-batch-safety input { width:15px; height:15px; accent-color:var(--blue); }
.filename-standard { display:inline-flex; padding:3px 7px; color:#267048; border-radius:999px; background:#e8f7ef; white-space:nowrap; font-size:9px; }
.filename-standard.warning { color:#98463d; background:#fdecea; }
.push-batch-footer { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:15px 24px; border-top:1px solid #dce5ea; background:#f7fafb; }
.push-batch-footer > span { color:#617885; font-size:10px; line-height:1.5; }
.push-batch-footer > div { display:flex; flex:0 0 auto; gap:8px; }
.push-batch-footer button { min-height:38px; padding:0 16px; color:var(--blue); border:1px solid #b9ccd6; background:#fff; white-space:nowrap; }
.push-batch-footer button.primary { color:#fff; border-color:var(--blue); background:var(--blue); }
.push-batch-footer button:disabled,.push-batch-common button:disabled { opacity:.55; cursor:not-allowed; }
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
  .push-batch-common { grid-template-columns:1fr; }
  .push-batch-summary { grid-column:auto; }
  .push-batch-footer { align-items:stretch; flex-direction:column; }
  .push-batch-footer > div { display:grid; grid-template-columns:1fr 1fr; }
  .push-batch-footer > div .primary { grid-column:1/-1; }
}

/* 与生产指挥中心统一的文件派发视觉层 */
.dispatch-hero { position:relative; min-height:160px; display:grid; grid-template-columns:minmax(460px,1fr) 330px; align-items:center; overflow:hidden; padding:24px 34px; border:1px solid rgba(255,255,255,.72); border-radius:14px; background:linear-gradient(90deg,rgba(246,252,255,.98) 0%,rgba(238,248,253,.9) 43%,rgba(7,48,76,.04) 70%),var(--dispatch-hero-image) center 53%/cover no-repeat; box-shadow:0 12px 30px rgba(22,62,89,.07); animation:surface-in .46s cubic-bezier(.2,.75,.25,1) both; }
.dispatch-hero::after { content:""; position:absolute; inset:0; pointer-events:none; background:linear-gradient(116deg,transparent 0 47%,rgba(255,255,255,.28) 47.2%,transparent 47.8%); }
.dispatch-hero__copy,.dispatch-hero__slogan { position:relative; z-index:1; }
.dispatch-hero .eyebrow { display:block; margin:0 0 5px; color:#237fb7; font-size:10px; font-weight:700; letter-spacing:1.4px; }
.dispatch-hero h1 { margin:0 0 7px; color:#153f5d; font-size:38px; line-height:1.08; letter-spacing:1px; }
.dispatch-hero__copy>p { max-width:600px; margin:0; color:#607a8d; font-size:13px; line-height:1.7; }
.dispatch-source-status { display:flex; flex-wrap:wrap; gap:7px; margin-top:12px; }
.dispatch-source-status span { display:inline-flex; align-items:center; gap:6px; padding:4px 8px; color:#537084; border:1px solid rgba(93,145,170,.15); border-radius:999px; background:rgba(255,255,255,.68); font-size:9px; }
.dispatch-source-status i,.confirm-machine small i { width:7px; height:7px; display:inline-block; flex:0 0 auto; border-radius:50%; background:#e49a37; box-shadow:0 0 0 3px rgba(228,154,55,.09); }
.dispatch-source-status i.ready,.confirm-machine small i.ready { background:#18ad80; box-shadow:0 0 0 3px rgba(24,173,128,.09); }
.dispatch-hero__slogan { justify-self:end; margin:0 12px 0 0; color:#eefaff; text-align:right; text-shadow:0 2px 10px rgba(4,37,58,.8); transform:rotate(-6deg); }
.dispatch-hero__slogan strong,.dispatch-hero__slogan span { display:block; }
.dispatch-hero__slogan strong { font-size:17px; letter-spacing:4px; }
.dispatch-hero__slogan span { margin-top:5px; font-size:12px; letter-spacing:3px; }
.dispatch-flow { grid-template-columns:repeat(4,minmax(0,1fr)); gap:0; overflow:hidden; margin:12px 0 0; border-color:#dbe6ec; border-radius:11px; box-shadow:0 7px 22px rgba(22,62,89,.05); }
.dispatch-flow>div { min-height:64px; gap:13px; padding:12px 28px; border-right:0; background:#fff; }
.dispatch-flow>div::after { content:""; width:82px; height:1px; right:-39px; background:repeating-linear-gradient(90deg,#bed2dd 0 4px,transparent 4px 7px); }
.dispatch-flow i { width:32px; height:32px; color:#58788e; border-color:#bad0dd; font-size:14px; font-weight:700; }
.dispatch-flow strong { color:#294b63; font-size:12px; }
.dispatch-flow small { color:#7a91a1; font-size:9px; }
.dispatch-flow .active { background:linear-gradient(135deg,#f5fbfe,#fff); }
.dispatch-flow .active i { border-color:#258ee0; background:linear-gradient(135deg,#258ee0,#20a8e7); box-shadow:0 6px 14px rgba(37,142,224,.2); }
.dispatch-flow .complete i { color:#fff; border-color:#22aa88; background:linear-gradient(135deg,#16a39d,#42c9a0); }
.dispatch-push-scope { display:flex; align-items:center; gap:8px; margin:8px 0 0; padding:8px 12px; color:#607b8e; border:1px solid #dbe9ef; border-left:3px solid #16a39d; border-radius:7px; background:#f4fafb; font-size:10px; line-height:1.6; }
.dispatch-push-scope .app-icon { color:#16a39d; }
.dispatch-notice { margin:10px 0 0; border:1px solid #cae6e8; border-left:3px solid #16a39d; border-radius:8px; }
.dispatch-notice.error { border-color:#f0ccca; border-left-color:#df5961; }
.dispatch-workspace.download-workspace { grid-template-columns:248px minmax(0,1fr); gap:12px; margin-top:12px; align-items:stretch; }
.dispatch-workspace>.panel { margin-bottom:0; }
.dispatch-workspace>.panel,.dispatch-history-panel { overflow:hidden; border:1px solid #dbe6ec; border-radius:11px; box-shadow:0 7px 22px rgba(22,62,89,.05); animation:surface-in .44s cubic-bezier(.2,.75,.25,1) both; }
.dispatch-files-panel { animation-delay:.05s!important; }
.dispatch-confirm-panel { animation-delay:.1s!important; }
.panel-title { min-height:48px; padding:0 13px; border-bottom:1px solid #e4edf1; background:linear-gradient(90deg,#fff,#fbfdfe); }
.panel-title>div { gap:7px; }
.panel-title>div>small { color:#7c92a2; font-size:10px; font-weight:700; }
.panel-title h2 { color:#193c57; font-size:14px; }
.panel-title>p { color:#6e8799; font-size:9px; }
.dispatch-section-icon { width:27px!important; height:27px!important; display:grid!important; place-items:center; padding:0!important; color:#16a39d!important; border:0!important; border-radius:8px!important; background:#e8f7f6!important; font-size:inherit!important; }
.dispatch-section-icon.blue { color:#237fb7!important; background:#eaf5fb!important; }
.dispatch-section-icon.cyan { color:#0ca6b1!important; background:#e6f7f8!important; }
.dispatch-section-icon.green { color:#2aa473!important; background:#eaf7f1!important; }
.project-search,.file-name-search { border-bottom-color:#e4edf1; background:#fbfdfe; }
.project-search { padding:11px 12px; }
.project-search input,.project-search .app-select { height:36px; border-color:#cbdde6; border-radius:7px; background:#fff; }
.project-search button,.file-name-search button { border-color:#bdd7e4; border-radius:7px; color:#237fb7; background:#f8fcfe; }
.directory-panel { position:relative; display:grid; grid-template-columns:minmax(0,1fr); grid-template-rows:auto auto minmax(0,1fr); min-height:0; }
.directory-panel .panel-title>div,.directory-panel .project-search { min-width:0; }
.directory-panel .project-select-row .app-select { width:auto; min-width:0; flex:1 1 0; }
.directory-panel .project-select-row>button { flex:0 0 auto; }
.directory-scroll { grid-area:3 / 1 / 4 / -1; position:absolute; inset:0; min-height:0; max-height:none; overflow:auto; overscroll-behavior:contain; scrollbar-gutter:stable; }
.project-node-row { border-radius:7px; }
.project-node-row:hover { background:#f2f8fb; }
.project-node-row.selected { background:linear-gradient(90deg,#e4f6fb,#eef9fb); box-shadow:inset 3px 0 #268fd0; }
.project-folder { border-color:#3b9be0; background:#e7f5ff; }
.project-folder::before { border-color:#3b9be0; background:#e7f5ff; }
.selected-directory { min-height:39px; padding:8px 13px; border-bottom-color:#dfeaf0; background:linear-gradient(90deg,#f0f8fb,#f8fcfd); }
.selected-directory span { color:#7890a0; }
.selected-directory strong { color:#24618a; font-size:10px; }
.file-name-search { padding:9px 13px; }
.file-name-search>div { width:min(355px,100%); }
.file-name-search input { height:34px; border-color:#cbdde6; border-radius:7px; }
.download-batch-toolbar { min-height:44px; padding:7px 13px; border-bottom-color:#e4edf1; background:#fff; }
.download-batch-toolbar button { min-height:31px; border-color:#c9dce6; border-radius:7px; }
.download-batch-toolbar button.primary { border-color:#258dda; background:linear-gradient(135deg,#258dda,#1ca5d7); box-shadow:0 5px 12px rgba(35,133,190,.16); }
.mobile-selection-bar,.mobile-selection-bar-spacer { display:none; }
.source-file-table { min-width:820px; }
.dispatch-table th,.dispatch-table td { height:39px; padding:7px 9px; border-bottom-color:#e7eef2; }
.source-file-table th { color:#536f83; background:#f0f6f9; font-size:9px; }
.source-file-table thead .file-download-cell { background:#f0f6f9; }
.dispatch-table tbody tr:hover { background:#f7fbfd; }
.dispatch-table tbody tr.selected { background:#eef8fb; box-shadow:inset 3px 0 #2696c8; }
.file-download-cell { width:100px; }
.file-download-cell button { min-height:29px; padding:4px 8px; border-color:#c5dbe5; border-radius:6px; background:#f9fcfd; font-size:9px; }
.filename-standard { color:#168c67; background:#e8f7f1; }
.filename-standard.warning { color:#d48328; background:#fff3e3; }
.dispatch-pagination { padding:9px 13px 0; }
.dispatch-pagination button { border-radius:6px; }
.dispatch-download-hint { margin:11px 14px 13px; font-size:9px; line-height:1.55; }
.dispatch-confirm-panel { min-width:0; grid-column:1/-1; align-self:start; }
.dispatch-confirm-content { display:grid; grid-template-columns:140px minmax(240px,1fr) minmax(230px,.8fr) 210px; align-items:center; gap:14px; height:auto; padding:14px 15px; box-sizing:border-box; }
.confirm-label { color:#627d90; font-size:10px; font-weight:700; }
.confirm-label { align-self:end; }
.confirm-total { grid-column:1; margin:0; color:#234b68; font-size:12px; }
.confirm-total strong { margin-right:3px; color:#154a70; font-size:25px; font-variant-numeric:tabular-nums; }
.confirm-file-list { grid-column:2; grid-row:1/3; min-height:86px; display:flex; flex-direction:column; gap:7px; padding:10px 12px; border:1px solid #dbe6ec; border-radius:7px; background:#fbfdfe; }
.confirm-file-list>span { overflow:hidden; color:#4b687d; text-overflow:ellipsis; white-space:nowrap; font-size:9px; }
.confirm-file-list>button { align-self:flex-end; min-height:22px; margin-top:auto; padding:0; color:#2586bd; border:0; background:transparent; font-size:9px; }
.confirm-file-list.empty { align-items:center; justify-content:center; gap:8px; color:#8aa0af; text-align:center; }
.confirm-file-list.empty>span { color:#8aa0af; white-space:normal; line-height:1.5; }
.confirm-machine { grid-column:3; grid-row:1/3; margin:0; padding:0; border:0; }
.confirm-machine label { display:flex; align-items:center; gap:6px; margin-bottom:8px; color:#294f69; font-size:11px; font-weight:700; }
.confirm-machine .app-select { width:100%; }
.confirm-machine small { display:flex; align-items:flex-start; gap:6px; margin-top:8px; color:#7b91a1; font-size:9px; line-height:1.45; }
.confirm-machine small i { margin-top:3px; }
.confirm-dispatch-button { min-height:40px; display:flex; align-items:center; justify-content:center; grid-column:4; grid-row:1/3; gap:7px; margin:0; padding:0 12px; color:#fff; border:0; border-radius:7px; background:linear-gradient(135deg,#238bdb,#149fd8); box-shadow:0 8px 18px rgba(32,137,202,.22); font-size:11px; font-weight:700; }
.confirm-dispatch-button:hover:not(:disabled) { transform:translateY(-1px); box-shadow:0 10px 22px rgba(32,137,202,.28); }
.confirm-dispatch-button:disabled { opacity:.48; cursor:not-allowed; box-shadow:none; }
.dispatch-history-panel { margin-top:12px; }
.history-table th { color:#dcecf2; background:linear-gradient(90deg,#0b4f76,#075f86); }
.dispatch-status.sent { color:#168c67; background:#e8f7f1; }
.history-actions button { border-radius:6px; }
.dispatch-empty-history i { color:#237fb7; border:0; background:#eaf5fb; }
.push-batch-backdrop { backdrop-filter:blur(5px); }
.push-batch-dialog { border:1px solid rgba(255,255,255,.25); border-radius:14px; box-shadow:0 28px 80px rgba(5,25,38,.32); }
.push-batch-header { background:linear-gradient(120deg,#0a4e73,#087e9d); }
.push-batch-header button,.push-batch-common button,.push-batch-remove,.push-batch-footer button { border-radius:7px; }
.push-batch-summary,.push-batch-error,.push-batch-progress { border-radius:7px; }
.push-batch-table-wrap { border-radius:8px; }
.push-batch-footer button.primary { border-color:#238bd8; background:linear-gradient(135deg,#238bd8,#159fd3); }

@media (max-width:1120px) {
  .dispatch-workspace.download-workspace { grid-template-columns:232px minmax(0,1fr); }
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
  .dispatch-hero { grid-template-columns:1fr; min-height:190px; padding:22px 18px; background:linear-gradient(90deg,rgba(246,252,255,.97),rgba(238,248,253,.88)),var(--dispatch-hero-image) center/cover; }
  .dispatch-hero h1 { font-size:31px; }
  .dispatch-hero__copy>p { font-size:12px; }
  .dispatch-hero__slogan { display:none; }
  .dispatch-source-status { gap:5px; }
  .dispatch-flow { grid-template-columns:1fr; }
  .dispatch-flow>div { min-height:58px; padding:10px 18px; border-bottom:1px solid #e7eef2; }
  .dispatch-flow>div::after { display:none; }
  .dispatch-push-scope { align-items:flex-start; }
  .file-name-search { align-items:stretch; }
  .download-batch-toolbar { align-items:stretch; }
  .dispatch-confirm-content { display:flex; height:auto; }
  .confirm-file-list { min-height:100px; }
  .confirm-machine { margin-top:13px; padding-top:12px; border-top:1px solid #e5edf1; }
  .confirm-dispatch-button { margin-top:14px; }
  .mobile-selection-bar-spacer { display:block; height:calc(72px + env(safe-area-inset-bottom)); }
  .mobile-selection-bar { position:fixed; z-index:950; right:0; bottom:0; left:0; display:grid; grid-template-columns:auto auto minmax(0,1fr); align-items:center; gap:8px; padding:10px max(12px,env(safe-area-inset-right)) calc(10px + env(safe-area-inset-bottom)) max(12px,env(safe-area-inset-left)); border-top:1px solid #c8dbe5; background:rgba(255,255,255,.97); box-shadow:0 -10px 30px rgba(14,55,80,.16); }
  .mobile-selection-bar>span { min-width:54px; display:flex; align-items:baseline; gap:3px; color:#6a8292; white-space:nowrap; }
  .mobile-selection-bar>span strong { color:#176b98; font-size:20px; font-variant-numeric:tabular-nums; }
  .mobile-selection-bar>span small { font-size:9px; }
  .mobile-selection-bar button { min-height:44px; padding:0 12px; color:#237fb7; border:1px solid #bfd4df; border-radius:7px; background:#f8fcfe; white-space:nowrap; font-size:11px; font-weight:700; }
  .mobile-selection-bar button.primary { min-width:0; display:flex; align-items:center; justify-content:center; gap:6px; color:#fff; border-color:#238bd8; background:linear-gradient(135deg,#238bd8,#159fd3); box-shadow:0 7px 16px rgba(32,137,202,.22); }
  .mobile-selection-bar button:disabled { opacity:.5; cursor:not-allowed; box-shadow:none; }
}
@media (prefers-reduced-motion:reduce) {
  .dispatch-hero,.dispatch-workspace>.panel,.dispatch-history-panel { animation:none; }
  .confirm-dispatch-button { transition:none; }
}
</style>
