<script setup>
import { computed, onActivated, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CatalogTreeNode from '../components/CatalogTreeNode.vue'
import { parseCuttingInstructionFilename } from '../utils/cuttingInstructionName.js'
import {
  cancelCuttingDispatch,
  createCuttingDispatch,
  executeCuttingDispatch,
  getCuttingStatus,
  listCuttingDispatches,
  listCuttingDownloads,
  listCuttingMachines,
  listCuttingProjects,
  listCuttingCatalogs,
  listCuttingFiles,
  downloadCuttingFile,
} from '../api/reports'

const props = defineProps({ mode: { type: String, required: true, validator: (value) => ['download', 'push'].includes(value) } })
const isDownload = computed(() => props.mode === 'download')
const route = useRoute()
const router = useRouter()
let querySequence = 0
const dispatchForm = reactive({ machineIp: '', material: '', thickness: '', quantity: 1 })
const systemStatus = ref(null)
const rows = ref([])
const histories = ref([])
const machines = ref([])
const pushFiles = ref([])
const selectedPushFileId = ref('')
const recognizedInstruction = ref(null)
const pushFileDraft = reactive({ keyword: '', projectId: '', catalogKey: '' })
const pushFileFilters = ref({ keyword: '', projectId: '', catalogKey: '' })
const pushFilePage = ref(1)
const pushFilePageSize = 20
const downloadingId = ref('')
const downloadedRows = reactive({})
const selectedDownloadFiles = ref(new Map())
const batchDownloading = ref(false)
const batchProgress = reactive({ completed: 0, total: 0 })
const loading = ref(false)
const saving = ref(false)
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

const activeFile = computed(() => pushFiles.value.find((row) => row.download_id === selectedPushFileId.value) || null)
const pushFileProjects = computed(() => {
  const options = new Map()
  pushFiles.value.forEach((item) => {
    const key = downloadProjectKey(item)
    const project = item.project_name || '未分类项目'
    const label = item.ship_no ? `${project} · ${item.ship_no}` : project
    if (!options.has(key)) options.set(key, { key, label })
  })
  return [...options.values()].sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
})
const pushFileCatalogs = computed(() => {
  const options = new Map()
  pushFiles.value
    .filter((item) => !pushFileDraft.projectId || downloadProjectKey(item) === pushFileDraft.projectId)
    .forEach((item) => {
      const key = downloadCatalogKey(item)
      if (!options.has(key)) options.set(key, { key, label: downloadCatalogLabel(item) })
    })
  return [...options.values()].sort((left, right) => left.label.localeCompare(right.label, 'zh-CN'))
})
const filteredPushFiles = computed(() => {
  const keyword = pushFileFilters.value.keyword.toLowerCase()
  return pushFiles.value.filter((item) => {
    const searchable = `${item.filename || ''} ${item.document_no || ''} ${item.relative_path || ''}`.toLowerCase()
    return (!keyword || searchable.includes(keyword))
      && (!pushFileFilters.value.projectId || downloadProjectKey(item) === pushFileFilters.value.projectId)
      && (!pushFileFilters.value.catalogKey || downloadCatalogKey(item) === pushFileFilters.value.catalogKey)
  })
})
const pushFileTotalPages = computed(() => Math.max(1, Math.ceil(filteredPushFiles.value.length / pushFilePageSize)))
const visiblePushFiles = computed(() => {
  const start = (pushFilePage.value - 1) * pushFilePageSize
  return filteredPushFiles.value.slice(start, start + pushFilePageSize)
})
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
const selectedDownloadCount = computed(() => selectedDownloadFiles.value.size)
const selectableRows = computed(() => rows.value.filter((row) => row.path_available && systemStatus.value?.can_download))
const allVisibleFilesSelected = computed(() => (
  selectableRows.value.length > 0
  && selectableRows.value.every((row) => selectedDownloadFiles.value.has(fileKey(row)))
))
const someVisibleFilesSelected = computed(() => (
  !allVisibleFilesSelected.value
  && selectableRows.value.some((row) => selectedDownloadFiles.value.has(fileKey(row)))
))
const selectedPath = computed(() => {
  if (!selectedProject.value) return '请选择项目和目录'
  const catalog = selectedCatalog.value
  return catalog ? `${selectedProject.value.project_name} / ${catalogPath(catalog)}` : selectedProject.value.project_name
})
onBeforeUnmount(() => { querySequence += 1 })

function showNotice(message, type = 'info') {
  notice.value = message
  noticeType.value = type
}

function fileKey(file) {
  return JSON.stringify([file.document_id, file.version_id, file.normalized_path])
}

function clearSelectedDownloads() {
  selectedDownloadFiles.value = new Map()
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

function setDownloadSelected(file, selected) {
  const next = new Map(selectedDownloadFiles.value)
  const key = fileKey(file)
  if (selected) next.set(key, file)
  else next.delete(key)
  selectedDownloadFiles.value = next
}

function toggleVisibleDownloads(selected) {
  const next = new Map(selectedDownloadFiles.value)
  for (const file of selectableRows.value) {
    const key = fileKey(file)
    if (selected) next.set(key, file)
    else next.delete(key)
  }
  selectedDownloadFiles.value = next
}

async function saveFileToSystem(file) {
  const result = await downloadCuttingFile({ document_id: file.document_id, version_id: file.version_id || null, file_path: file.normalized_path })
  downloadedRows[fileKey(file)] = true
  return result
}

async function downloadFile(file) {
  if (!isDownload.value || downloadingId.value || batchDownloading.value || loading.value || !file.path_available || !systemStatus.value?.can_download) return
  const key = fileKey(file)
  downloadingId.value = key
  showNotice(`正在将 ${file.document_no} 保存到系统…`)
  try {
    const result = await saveFileToSystem(file)
    setDownloadSelected(file, false)
    showNotice(`${result.filename} 已保存到系统，可前往“文件推送”页面使用。`)
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    downloadingId.value = ''
  }
}

async function downloadSelectedFiles() {
  if (!isDownload.value || batchDownloading.value || downloadingId.value || loading.value) return
  const files = [...selectedDownloadFiles.value.values()].filter((file) => file.path_available)
  if (!files.length || !systemStatus.value?.can_download) {
    showNotice('请先选择可保存的文件。', 'error')
    return
  }
  batchDownloading.value = true
  Object.assign(batchProgress, { completed: 0, total: files.length })
  let succeeded = 0
  const failed = []
  try {
    for (const [index, file] of files.entries()) {
      downloadingId.value = fileKey(file)
      showNotice(`正在批量保存 ${index + 1}/${files.length}：${file.document_no}`)
      try {
        await saveFileToSystem(file)
        setDownloadSelected(file, false)
        succeeded += 1
      } catch (error) {
        failed.push(`${file.document_no}：${error.message}`)
      } finally {
        batchProgress.completed = index + 1
      }
    }
    if (failed.length) {
      showNotice(`批量保存完成：成功 ${succeeded} 项，失败 ${failed.length} 项。${failed[0]}`, 'error')
    } else {
      showNotice(`已将 ${succeeded} 个文件全部保存到系统，可前往“文件推送”页面使用。`)
    }
  } finally {
    downloadingId.value = ''
    batchDownloading.value = false
  }
}

async function loadHistory() {
  if (isDownload.value) return
  const data = await listCuttingDispatches({ page: 1, pageSize: 10 })
  histories.value = data.items
}

async function loadMachines() {
  if (isDownload.value || !systemStatus.value?.magic_box?.configured) return
  try {
    const data = await listCuttingMachines()
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

async function loadPushFiles() {
  if (isDownload.value) return
  const data = await listCuttingDownloads({ page: 1, pageSize: 100 })
  pushFiles.value = (data.items || []).filter((item) => item.available && !item.is_simulation)
  if (!pushFiles.value.some((item) => item.download_id === selectedPushFileId.value)) {
    selectedPushFileId.value = pushFiles.value[0]?.download_id || ''
  }
  pushFilePage.value = Math.min(pushFilePage.value, pushFileTotalPages.value)
}

function applyPushFileFilters() {
  pushFileFilters.value = {
    keyword: pushFileDraft.keyword.trim(),
    projectId: pushFileDraft.projectId,
    catalogKey: pushFileDraft.catalogKey,
  }
  pushFilePage.value = 1
  if (activeFile.value && !filteredPushFiles.value.some((item) => item.download_id === activeFile.value.download_id)) {
    selectedPushFileId.value = ''
  }
}

function resetPushFileFilters() {
  Object.assign(pushFileDraft, { keyword: '', projectId: '', catalogKey: '' })
  pushFileFilters.value = { keyword: '', projectId: '', catalogKey: '' }
  pushFilePage.value = 1
}

function selectPushFile(item) {
  selectedPushFileId.value = item.download_id
}

watch(selectedPushFileId, () => {
  dispatchForm.material = ''
  dispatchForm.thickness = ''
  dispatchForm.quantity = 1
  const parsed = parseCuttingInstructionFilename(activeFile.value?.filename)
  recognizedInstruction.value = parsed
  if (!parsed) return
  dispatchForm.material = parsed.material
  dispatchForm.thickness = parsed.thickness
}, { flush: 'sync' })

function changePushFilePage(nextPage) {
  if (nextPage < 1 || nextPage > pushFileTotalPages.value) return
  pushFilePage.value = nextPage
}

function downloadProjectKey(item) {
  return String(item.project_id || '__unclassified__')
}

function downloadCatalogKey(item) {
  if (Array.isArray(item.catalog_path) && item.catalog_path.length) {
    return item.catalog_path.map((node) => String(node.catalog_id || node.title || '')).join('>')
  }
  return String(item.catalog_id || '__unclassified__')
}

function downloadCatalogLabel(item) {
  const titles = Array.isArray(item.catalog_path)
    ? item.catalog_path.map((node) => String(node.title || '').trim()).filter(Boolean)
    : []
  if (titles[0] && item.project_name && titles[0] === String(item.project_name).trim()) titles.shift()
  return titles.join(' / ') || item.catalog_title || '未分类目录'
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
  projectLoading.value = true
  let projectToOpen = null
  try {
    const data = await listCuttingProjects()
    if (sequence !== querySequence) return
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
    clearSelectedDownloads()
    total.value = 0
    page.value = 1
    notice.value = ''
  } catch (error) {
    if (sequence === querySequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) projectLoading.value = false
  }
  if (projectToOpen && sequence === querySequence) await selectProject(projectToOpen)
}

function resetProjectSelection() {
  rememberSelectedProject(selectedProjectId.value)
  querySequence += 1
  catalogLoading.value = false
  loading.value = false
  selectedProject.value = null
  selectedCatalog.value = null
  projectExpanded.value = false
  catalogTree.value = []
  expandedCatalogIds.value = new Set()
  rows.value = []
  clearSelectedDownloads()
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
  selectedProjectId.value = String(project.project_id)
  rememberSelectedProject(selectedProjectId.value)
  selectedProject.value = project
  projectExpanded.value = true
  selectedCatalog.value = null
  catalogTree.value = []
  expandedCatalogIds.value = new Set()
  rows.value = []
  clearSelectedDownloads()
  total.value = 0
  page.value = 1
  catalogLoading.value = true
  try {
    const data = await listCuttingCatalogs(project.project_id)
    if (sequence !== querySequence) return
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
    if (sequence === querySequence) showNotice(error.message, 'error')
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
  clearSelectedDownloads()
  await loadFiles(1)
}

async function applyFileSearch() {
  if (fileKeyword.value.trim() !== appliedFileKeyword.value) clearSelectedDownloads()
  await loadFiles(1)
}

async function loadFiles(targetPage = 1) {
  if (!selectedProject.value || !selectedCatalog.value) return
  const sequence = ++querySequence
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
    })
    if (sequence !== querySequence) return
    rows.value = data.items
    total.value = data.total
    page.value = targetPage
    appliedFileKeyword.value = keyword
    notice.value = ''
  } catch (error) {
    if (sequence === querySequence) showNotice(error.message, 'error')
  } finally {
    if (sequence === querySequence) loading.value = false
  }
}

async function changePage(nextPage) {
  if (loading.value || nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
  await loadFiles(nextPage)
}

async function saveDispatch(executeNow = false) {
  if (isDownload.value || saving.value) return
  if (!activeFile.value) {
    showNotice('请先选择一个切割文件。', 'error')
    return
  }
  if (executeNow && !window.confirm(`确认将文件“${activeFile.value.filename}”推送到机床 ${dispatchForm.machineIp}？\n任务名称将使用该文件名。`)) return
  saving.value = true
  try {
    const item = await createCuttingDispatch({
      download_id: activeFile.value.download_id,
      machine_ip: dispatchForm.machineIp,
      material: dispatchForm.material || null,
      thickness: dispatchForm.thickness === '' ? null : Number(dispatchForm.thickness),
      quantity: Number(dispatchForm.quantity),
    })
    if (executeNow) {
      operatingId.value = item.dispatch_id
      const completed = await executeCuttingDispatch(item.dispatch_id)
      showNotice(`文件已上传魔盒并指派到 ${completed.machine_ip}，任务名称使用实际上传文件名。`)
    } else {
      showNotice(`已保存派发任务 ${item.dispatch_id}，当前状态：${item.status_label}。`)
    }
    await loadHistory()
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    saving.value = false
    operatingId.value = ''
  }
}

async function executeSaved(item) {
  if (operatingId.value) return
  if (!window.confirm(`确认执行任务 ${item.dispatch_id}？\n文件：${item.document_no}\n机床：${item.machine_ip}`)) return
  operatingId.value = item.dispatch_id
  try {
    const completed = await executeCuttingDispatch(item.dispatch_id)
    showNotice(`推送完成，魔盒任务 UUID：${completed.remote_task_id}`)
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    operatingId.value = ''
    await loadHistory()
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

function formatTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 19) : '--'
}

function fileExtension(row) {
  const source = String(row.document_no || row.physical_file_name || row.normalized_path || '')
  const filename = source.split(/[\\/]/).pop() || ''
  const dot = filename.lastIndexOf('.')
  return dot > 0 ? filename.slice(dot).toUpperCase() : '--'
}

function formatBytes(value) {
  if (value == null) return '--'
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 ** 2).toFixed(1)} MB`
}

function downloadSourceLabel(item) {
  return item.source_mode === 'http' ? 'FO文件服务' : item.source_mode === 'local' ? '本地目录' : '其他来源'
}

onMounted(async () => {
  try {
    if (isDownload.value) {
      const [statusData] = await Promise.all([
        getCuttingStatus('download'),
        loadProjects(),
      ])
      systemStatus.value = statusData
      return
    }

    loading.value = true
    systemStatus.value = await getCuttingStatus('push')
    await Promise.all([loadPushFiles(), loadMachines(), loadHistory()])
  } catch (error) {
    showNotice(error.message, 'error')
  } finally {
    if (!isDownload.value) loading.value = false
  }
})

onActivated(async () => {
  if (isDownload.value) return
  try {
    await loadPushFiles()
  } catch (error) {
    showNotice(`刷新系统已保存文件失败：${error.message}`, 'error')
  }
})
</script>

<template>
  <section class="page-hero dispatch-hero">
    <div>
      <span class="eyebrow">{{ isDownload ? 'FILE DOWNLOAD' : 'FILE PUSH' }}</span>
      <h2>{{ isDownload ? '文件下载' : '文件推送' }}</h2>
      <p>{{ isDownload ? '按项目目录逐级定位文件，将对应版本保存到系统，供后续推送使用。' : '选择系统已保存文件，填写目标机床和加工参数，确认后推送。' }}</p>
    </div>
    <div class="dispatch-stage-badge">
      <span>{{ systemStatus?.safe_mode ? '安全联调模式' : '生产数据模式' }}</span>
      <strong>{{ systemStatus?.pdm?.label || '正在检查数据源' }} · {{ systemStatus?.file_source?.label || '--' }}</strong>
      <small>{{ systemStatus?.file_source?.message || '正在加载系统状态' }}</small>
    </div>
  </section>

  <div v-if="isDownload" class="dispatch-flow" aria-label="下载流程">
    <div class="active"><i>01</i><span><strong>选择项目目录</strong><small>项目 · 分段 · 文件分类</small></span></div>
    <div><i>02</i><span><strong>保存文件到系统</strong><small>准确版本 · 保存记录可追溯</small></span></div>
  </div>
  <p v-else class="dispatch-push-scope" role="note">确认推送后，系统会校验并复用已下载的准确版本，必要时先从 FO 下载，再上传魔盒并指派机床。每次真实推送都需要人工确认，不会按查询结果批量执行。</p>

  <div v-if="notice" class="dispatch-notice" :class="{ error: noticeType === 'error' }" role="status">
    <span>{{ notice }}</span>
    <button type="button" @click="notice = ''">关闭</button>
  </div>

  <div class="dispatch-workspace" :class="{ 'download-workspace': isDownload, 'push-workspace': !isDownload }">
    <aside v-if="isDownload" class="panel directory-panel">
      <div class="panel-title">
        <div><span>01</span><h2>项目目录</h2></div>
        <p>{{ projectCountLabel }}</p>
      </div>
      <form class="project-search" @submit.prevent="openSelectedProject">
        <label for="project-keyword">项目关键词 <small>项目名称或船号</small></label>
        <input id="project-keyword" v-model="projectKeyword" type="search" placeholder="输入关键词筛选" autocomplete="off" :disabled="projectLoading" @input="applyProjectKeyword" />
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

    <section v-if="isDownload" class="panel dispatch-files-panel">
      <div class="panel-title">
        <div><span>02</span><h2>目录文件</h2></div>
        <p>{{ selectedCatalog ? `共 ${total} 条 · 当前第 ${page} 页` : '请选择左侧目录' }}</p>
      </div>
      <div class="selected-directory"><span>当前位置</span><strong>{{ selectedPath }}</strong></div>
      <form class="file-name-search" @submit.prevent="applyFileSearch">
        <label for="file-keyword">当前目录内查找文件</label>
        <div><input id="file-keyword" v-model.trim="fileKeyword" :disabled="!selectedCatalog" placeholder="输入文件名或中间产品" /><button type="submit" :disabled="!selectedCatalog || loading">{{ loading ? '查询中…' : '查询' }}</button></div>
      </form>
      <div class="download-batch-toolbar" aria-live="polite">
        <span>已选择 <strong>{{ selectedDownloadCount }}</strong> 项<small v-if="batchDownloading">正在保存 {{ batchProgress.completed }}/{{ batchProgress.total }}</small></span>
        <div><button type="button" :disabled="!selectedDownloadCount || batchDownloading" @click="clearSelectedDownloads">清空选择</button><button class="primary" type="button" :disabled="!selectedDownloadCount || batchDownloading || loading || !systemStatus?.can_download" @click="downloadSelectedFiles">{{ batchDownloading ? '批量保存中…' : '批量保存到系统' }}</button></div>
      </div>
      <div class="dispatch-table-wrap" :aria-busy="loading">
        <div v-if="loading" class="dispatch-loading" role="status">正在加载第 {{ requestedPage }} 页…</div>
        <table v-content-table class="dispatch-table" aria-label="文件查询结果">
          <thead>
            <tr><th class="download-check-cell"><input type="checkbox" :checked="allVisibleFilesSelected" :indeterminate="someVisibleFilesSelected" :disabled="!selectableRows.length || loading || batchDownloading" aria-label="全选当前页可保存文件" @change="toggleVisibleDownloads($event.target.checked)" /></th><th>文件名</th><th>中间产品</th><th>文件格式</th><th>版本</th><th>操作人</th><th>操作人工号</th><th>操作时间</th><th>切割状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in rows" :key="fileKey(row)" :class="{ selected: selectedDownloadFiles.has(fileKey(row)) }">
              <td class="download-check-cell"><input type="checkbox" :checked="selectedDownloadFiles.has(fileKey(row))" :disabled="loading || batchDownloading || !!downloadingId || !row.path_available || !systemStatus?.can_download" :aria-label="`选择文件 ${row.document_no}`" @change="setDownloadSelected(row, $event.target.checked)" /></td>
              <td class="file-name-cell"><strong>{{ row.document_no }}</strong></td>
              <td>{{ row.pbs_info || '--' }}</td>
              <td class="file-extension">{{ fileExtension(row) }}</td>
              <td>{{ row.version || row.physical_version_code?.trim() || row.physical_version_no || '--' }}</td>
              <td>{{ row.operator || row.physical_file_operator || '--' }}</td>
              <td>{{ row.operator_job_no || row.physical_file_operator_job_no || '--' }}</td>
              <td>{{ formatTime(row.operate_time || row.physical_file_date) }}</td>
              <td><span class="dispatch-status" :class="row.status">{{ row.status_label }}</span></td>
              <td class="file-download-cell"><button type="button" :disabled="loading || batchDownloading || !!downloadingId || !row.path_available || !systemStatus?.can_download" :aria-label="`保存 ${row.document_no} 到系统`" @click="downloadFile(row)">{{ downloadingId === fileKey(row) ? '保存中…' : downloadedRows[fileKey(row)] ? '再次保存' : '保存到系统' }}</button><small v-if="downloadedRows[fileKey(row)]">本次已保存</small><small v-else-if="!row.path_available">{{ row.path_error || '路径不可用' }}</small></td>
            </tr>
            <tr v-if="!loading && rows.length === 0"><td colspan="10" class="dispatch-empty-row">{{ selectedCatalog ? appliedFileKeyword ? '当前目录没有匹配的文件' : '当前目录没有文件' : '请从左侧选择一个目录' }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="dispatch-pagination"><button type="button" :disabled="page <= 1 || loading" @click="changePage(page - 1)">上一页</button><span>第 {{ page }} / {{ totalPages }} 页</span><button type="button" :disabled="page >= totalPages || loading" @click="changePage(page + 1)">下一页</button></div>
      <p class="dispatch-download-hint">文件将从 FO 文件服务保存到系统，供魔盒推送使用。保存前会重新校验文件路径与版本。{{ systemStatus && !systemStatus.can_download ? '当前文件来源未启用，暂不可保存。' : '' }}</p>
    </section>

    <section v-if="!isDownload" class="panel push-file-picker">
      <div class="panel-title"><div><span>01</span><h2>选择系统已保存文件</h2></div><p>共 {{ filteredPushFiles.length }} 条可推送文件</p></div>
      <form class="push-file-filter" @submit.prevent="applyPushFileFilters">
        <label><span>文件名或来源路径</span><input v-model="pushFileDraft.keyword" placeholder="输入文件名关键字" /></label>
        <label><span>项目 / 船号</span><AppSelect v-model="pushFileDraft.projectId" @change="pushFileDraft.catalogKey = ''"><option value="">全部项目</option><option v-for="project in pushFileProjects" :key="project.key" :value="project.key">{{ project.label }}</option></AppSelect></label>
        <label><span>目录路径</span><AppSelect v-model="pushFileDraft.catalogKey"><option value="">全部目录</option><option v-for="catalog in pushFileCatalogs" :key="catalog.key" :value="catalog.key">{{ catalog.label }}</option></AppSelect></label>
        <div class="push-filter-actions"><button type="button" @click="resetPushFileFilters">重置</button><button class="primary" type="submit">筛选</button></div>
      </form>
      <div class="dispatch-table-wrap">
        <table v-content-table class="dispatch-table push-file-table" aria-label="可推送文件列表">
          <thead><tr><th>选择</th><th>文件名</th><th>项目 / 目录</th><th>版本 / 来源</th><th>大小</th><th>下载时间 / 下载人</th></tr></thead>
          <tbody>
            <tr v-for="item in visiblePushFiles" :key="item.download_id" :class="{ selected: item.download_id === selectedPushFileId }" @click="selectPushFile(item)">
              <td class="push-radio-cell"><input type="radio" name="push-file" :checked="item.download_id === selectedPushFileId" :aria-label="`选择推送文件 ${item.filename}`" @click.stop @change="selectPushFile(item)" /></td>
              <td class="file-name-cell"><strong>{{ item.filename }}</strong><small :title="item.relative_path">{{ item.relative_path }}</small></td>
              <td class="push-file-origin"><strong>{{ item.project_name || '未分类项目' }}</strong><small>{{ item.ship_no || '--' }} · {{ downloadCatalogLabel(item) }}</small></td>
              <td>{{ item.version || '--' }}<small class="dispatch-project-name">{{ downloadSourceLabel(item) }}</small></td>
              <td>{{ formatBytes(item.size) }}</td>
              <td>{{ formatTime(item.created_at) }}<small class="dispatch-project-name">{{ item.created_by_name || '--' }}</small></td>
            </tr>
            <tr v-if="!visiblePushFiles.length"><td colspan="6" class="dispatch-empty-row">{{ pushFiles.length ? '没有符合筛选条件的系统已保存文件' : '暂无可推送文件，请先到“文件下载”页面保存文件' }}</td></tr>
          </tbody>
        </table>
      </div>
      <div class="dispatch-pagination"><button type="button" :disabled="pushFilePage <= 1" @click="changePushFilePage(pushFilePage - 1)">上一页</button><span>第 {{ pushFilePage }} / {{ pushFileTotalPages }} 页</span><button type="button" :disabled="pushFilePage >= pushFileTotalPages" @click="changePushFilePage(pushFilePage + 1)">下一页</button></div>
      <p class="dispatch-download-hint">这里只显示系统中存在、校验有效且非模拟的文件；筛选不会自动创建或执行推送任务。</p>
    </section>

    <aside v-if="!isDownload" class="panel dispatch-config-panel">
      <div class="panel-title"><div><span>02</span><h2>派发参数</h2></div><p>选择单个文件后填写推送参数</p></div>
      <div class="dispatch-selected-file">
        <span>当前选择</span>
        <strong>{{ activeFile?.filename || '尚未选择文件' }}</strong>
        <small v-if="activeFile">版本 {{ activeFile.version || '--' }} · {{ activeFile.storage_path }}</small>
        <small v-if="recognizedInstruction" class="dispatch-name-recognition">
          已按标准名称填充：{{ recognizedInstruction.instructionTypeLabel }} · 中间产品 {{ recognizedInstruction.intermediateProduct }} · {{ recognizedInstruction.material }} · {{ recognizedInstruction.thickness.toFixed(1) }} mm · 顺序 {{ recognizedInstruction.sequence }}
        </small>
      </div>
      <form class="dispatch-config" @submit.prevent="saveDispatch(false)">
        <label><span>任务名称（自动）</span><input :value="activeFile?.filename || ''" disabled /><small>taskName 与实际上传文件名一致，包含扩展名。</small></label>
        <label><span>目标机床 IP</span><AppSelect v-model="dispatchForm.machineIp" required><option value="" disabled>请选择 SYS_CONFIG 中的机床</option><option v-for="machine in machines" :key="machine.machine_ip" :value="machine.machine_ip">{{ machine.server_name ? `${machine.server_name} · ` : '' }}{{ machine.machine_ip }}（{{ machine.online ? '在线' : '未从列表确认在线' }}）</option></AppSelect></label>
        <div class="dispatch-config-row">
          <label><span>材质</span><input v-model.trim="dispatchForm.material" placeholder="可选" /></label>
          <label><span>板厚（mm）</span><input v-model="dispatchForm.thickness" type="number" min="0" step="0.01" placeholder="0" /></label>
        </div>
        <label><span>加工数量</span><input v-model="dispatchForm.quantity" type="number" min="1" max="10000" step="1" required /></label>
        <div class="dispatch-task-id"><span>任务标识</span><code>提交时由系统生成唯一 ID</code></div>
        <div class="dispatch-warning"><strong>安全推送</strong><span>上传、指派分阶段记录。如上传超时导致结果不明，系统会停止自动重试，避免重复任务。</span></div>
        <div class="dispatch-submit-row"><button type="submit" :disabled="!activeFile?.available || saving">保存草稿</button><button class="primary" type="button" :disabled="!activeFile?.available || !dispatchForm.machineIp || !systemStatus?.can_push || saving" @click="saveDispatch(true)">{{ saving ? '处理中…' : '确认并推送' }}</button></div>
      </form>
    </aside>
  </div>

  <section v-if="!isDownload" class="panel dispatch-history-panel">
    <div class="panel-title"><div><span>03</span><h2>派发记录</h2></div><p>文件准备 → 魔盒上传 → 机床指派，各阶段独立记录</p></div>
    <div v-if="histories.length" class="dispatch-table-wrap"><table v-content-table class="dispatch-table history-table"><thead><tr><th>任务标识</th><th>文件名</th><th>机床IP</th><th>阶段 / 状态</th><th>魔盒UUID</th><th>失败原因</th><th>创建时间</th><th>操作</th></tr></thead><tbody><tr v-for="item in histories" :key="item.dispatch_id"><td><code>{{ item.task_identifier || item.dispatch_id }}</code></td><td>{{ item.document_no }}<small class="dispatch-project-name">数量 {{ item.quantity }}</small></td><td>{{ item.machine_ip }}</td><td><span class="dispatch-status" :class="item.status">{{ item.status_label }}</span><small class="dispatch-project-name">{{ item.stage_label }}</small></td><td><code>{{ item.remote_task_id || '--' }}</code></td><td class="history-error">{{ item.last_error || '--' }}</td><td>{{ formatTime(item.created_at) }}</td><td><div class="history-actions"><button v-if="item.can_execute" type="button" :disabled="!!operatingId" @click="executeSaved(item)">{{ item.remote_task_id ? '重试指派' : '执行推送' }}</button><button v-if="item.can_cancel" type="button" class="danger" :disabled="!!operatingId" @click="cancelSaved(item)">取消指派</button><span v-if="!item.can_execute && !item.can_cancel">--</span></div></td></tr></tbody></table></div>
    <div v-else class="dispatch-empty-history"><i>↗</i><strong>暂无派发记录</strong><span>选择文件和机床后可保存草稿，或明确确认后直接推送。</span></div>
  </section>
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
.dispatch-config label > span { display:block; margin-bottom:7px; color:#425767; font-size:11px; font-weight:700; }
.dispatch-workspace { display:grid; grid-template-columns:minmax(0,1.8fr) minmax(300px,.72fr); gap:22px; align-items:start; }
.dispatch-workspace.download-workspace { grid-template-columns:minmax(285px,.42fr) minmax(0,1.58fr); align-items:stretch; }
.dispatch-workspace.push-workspace { grid-template-columns:minmax(0,1.55fr) minmax(320px,.65fr); align-items:stretch; }
.dispatch-files-panel,.dispatch-config-panel { min-width:0; }
.download-workspace .dispatch-files-panel { display:flex; flex-direction:column; }
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
.push-radio-cell { width:48px; text-align:center!important; }
.push-radio-cell input { width:15px; height:15px; }
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
.dispatch-path-preview { display:flex; flex-direction:column; gap:7px; margin:18px 22px 22px; padding:15px 16px; border:1px solid #d8e3e8; background:#f7fafb; }
.dispatch-path-preview > span { color:#526a79; font-size:10px; font-weight:700; letter-spacing:1px; }
.dispatch-path-preview code { overflow-wrap:anywhere; color:#0d5d83; font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace; }
.dispatch-path-preview small { color:var(--muted); font-size:10px; }
.dispatch-config-panel { position:sticky; top:18px; }
.push-workspace .dispatch-config-panel { position:static; }
.dispatch-name-recognition { color:#167e85!important; font-weight:700; }
.push-workspace .dispatch-config { grid-template-columns:1fr; }
.push-workspace .dispatch-selected-file,.push-workspace .dispatch-warning,.push-workspace .dispatch-task-id,.push-workspace .dispatch-submit-row { grid-column:1/-1; }
.dispatch-selected-file { display:flex; flex-direction:column; gap:4px; margin:20px 22px 0; padding:14px 15px; border-left:4px solid var(--cyan); background:#eef8fa; }
.dispatch-selected-file span { color:var(--muted); font-size:9px; letter-spacing:1px; }
.dispatch-selected-file strong { overflow:hidden; color:var(--navy); text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.dispatch-selected-file small { color:#a26917; font-size:9px; }
.dispatch-config { display:grid; gap:13px; padding:18px 22px 24px; }
.dispatch-config label > small { display:block; margin-top:5px; color:var(--muted); font-size:9px; line-height:1.5; }
.dispatch-config-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
.dispatch-task-id { display:flex; flex-direction:column; gap:5px; padding:11px 12px; border:1px dashed #bdcbd5; background:#fafcfd; }
.dispatch-task-id span { color:#526a79; font-size:10px; font-weight:700; }
.dispatch-task-id code { color:#788a96; font-size:10px; }
.dispatch-warning { display:flex; flex-direction:column; gap:3px; padding:11px 12px; color:#7b581d; background:#fff6e4; }
.dispatch-warning strong { font-size:11px; }
.dispatch-warning span { font-size:9px; line-height:1.5; }
.dispatch-submit-row { display:grid; grid-template-columns:.8fr 1.2fr; gap:9px; }
.dispatch-submit-row button { width:100%; }
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
@media (max-width:1000px) {
  .dispatch-hero { align-items:flex-start; flex-direction:column; justify-content:center; padding:28px 0; }
  .dispatch-stage-badge { width:100%; min-width:0; }
  .dispatch-flow { grid-template-columns:1fr 1fr; }
  .dispatch-flow > div:nth-child(2) { border-right:0; }
  .dispatch-flow > div:nth-child(-n+2) { border-bottom:1px solid var(--line); }
  .dispatch-workspace,.dispatch-workspace.download-workspace,.dispatch-workspace.push-workspace { grid-template-columns:1fr; }
  .dispatch-config-panel { position:static; }
  .push-workspace .dispatch-config { grid-template-columns:1fr; }
  .directory-scroll { max-height:420px; }
  .push-file-filter { grid-template-columns:1fr 1fr; }
}
@media (max-width:560px) {
  .dispatch-flow,.dispatch-config-row { grid-template-columns:1fr; }
  .dispatch-flow > div { border-right:0; border-bottom:1px solid var(--line); }
  .dispatch-flow > div:last-child { border-bottom:0; }
  .dispatch-flow > div::after { display:none; }
  .file-name-search { align-items:stretch; flex-direction:column; gap:7px; }
  .file-name-search > div { width:100%; }
  .download-batch-toolbar { align-items:stretch; flex-direction:column; }
  .download-batch-toolbar > div button { flex:1; }
  .push-file-filter { grid-template-columns:1fr; }
  .push-filter-actions button { flex:1; }
}
</style>
