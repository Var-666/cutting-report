import type { Ref } from 'vue'
import type { CuttingCatalog, CuttingFile, CuttingProject, CuttingStatus } from '../types/cutting'
import { computed, ref } from 'vue'
import { listCuttingProjects, listCuttingCatalogs, listCuttingFiles } from '../api/dispatch'
import { cuttingFileKey as fileKey } from '../utils/cuttingDispatchBatch'
import { toError } from '../utils/errors'

export function useCuttingFileBrowser({ pageActive, systemStatus, isDispatching, initialProjectId, rememberSelectedProject, notice, showNotice }: {
  pageActive: Ref<boolean>
  systemStatus: Ref<CuttingStatus | null>
  isDispatching: () => boolean
  initialProjectId: string
  rememberSelectedProject: (projectId: string) => void
  notice: Ref<string>
  showNotice: (message: string, type?: string) => void
}) {
  let querySequence = 0
  let directoryController: AbortController | null = null
  const projectsLoaded = ref(false)

  function beginDirectoryRequest() {
    selectingAll.value = false
    loading.value = false
    directoryController?.abort()
    directoryController = new AbortController()
    return directoryController.signal
  }
  const rows = ref<CuttingFile[]>([])
  const selectedFiles = ref(new Map<string, CuttingFile>())
  const catalogSelectionRows = ref<CuttingFile[] | null>(null)
  const selectingAll = ref(false)
  const loading = ref(false)
  const page = ref(1)
  const requestedPage = ref(1)
  const pageSize = 20
  const total = ref(0)
  const projectOptions = ref<CuttingProject[]>([])
  const projectSearch = ref('')
  const projectSuggestions = computed(() => {
    const keyword = (projectSearch.value || '').trim().toLocaleLowerCase()
    return projectOptions.value
      .map((project) => ({ value: String(project.project_id), displayLabel: `${project.project_name}${project.ship_no ? ` · ${project.ship_no}` : ''}` }))
      .filter((option) => option.displayLabel.toLocaleLowerCase().includes(keyword))
  })
  const selectedProjectId = ref(initialProjectId)
  const projects = computed(() => {
    const selected = projectOptions.value.find((project) => String(project.project_id) === selectedProjectId.value)
    return selected ? [selected] : []
  })
  const catalogTree = ref<CuttingCatalog[]>([])
  const fileKeyword = ref('')
  const appliedFileKeyword = ref('')
  const fileError = ref('')
  const selectedProject = ref<CuttingProject | null>(null)
  const selectedCatalog = ref<CuttingCatalog | null>(null)
  const projectExpanded = ref(false)
  const expandedCatalogIds = ref(new Set<string>())
  const projectLoading = ref(false)
  const catalogLoading = ref(false)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
  const selectableRows = computed(() => (catalogSelectionRows.value ?? rows.value).filter((row) => row.path_available && systemStatus.value?.can_push))
  const allCatalogFilesSelected = computed(() => (
    (catalogSelectionRows.value !== null || total.value <= rows.value.length)
    && selectableRows.value.length > 0
    && selectableRows.value.every((row) => selectedFiles.value.has(fileKey(row)))
  ))
  const someCatalogFilesSelected = computed(() => (
    !allCatalogFilesSelected.value
    && selectedFiles.value.size > 0
  ))
  const selectedPath = computed(() => {
    if (!selectedProject.value) return '请选择项目和目录'
    const catalog = selectedCatalog.value
    return catalog ? `${selectedProject.value.project_name} / ${catalogPath(catalog)}` : selectedProject.value.project_name
  })

  function alreadySelectedDocument(files: Map<string, CuttingFile>, file: CuttingFile) {
    return [...files.values()].some((selected) => String(selected.document_id) === String(file.document_id))
  }

  function clearSelectedFiles() {
    if (selectingAll.value) {
      querySequence += 1
      directoryController?.abort()
      selectingAll.value = false
      loading.value = false
    }
    catalogSelectionRows.value = null
    selectedFiles.value = new Map()
  }

  function setFileSelected(file: CuttingFile, selected: boolean) {
    const next = new Map(selectedFiles.value)
    const key = fileKey(file)
    if (selected && !next.has(key) && next.size >= 100) {
      showNotice('已达 100 项上限，请先取消部分文件后再选择。', 'error')
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

  async function toggleCatalogFiles(selected: boolean) {
    if (loading.value || isDispatching() || !systemStatus.value?.can_push || !selectedProject.value || !selectedCatalog.value) return
    const sequence = ++querySequence
    const signal = beginDirectoryRequest()
    loading.value = true
    selectingAll.value = true
    try {
      if (catalogSelectionRows.value === null) {
        let files = rows.value
        if (total.value > rows.value.length) {
          files = []
          let filePage = 1
          let fileTotal = total.value
          do {
            const data = await listCuttingFiles({
              projectId: selectedProject.value.project_id,
              catalogId: selectedCatalog.value.catalog_id,
              keyword: appliedFileKeyword.value,
              page: filePage,
              pageSize: 100,
            }, { signal })
            if (signal.aborted || !pageActive.value || sequence !== querySequence) return
            if (!data.items.length && files.length < data.total) throw new Error('文件列表发生变化，请重新查询后全选。')
            files.push(...data.items)
            fileTotal = data.total
            filePage += 1
          } while ((filePage - 1) * 100 < fileTotal)
        }
        catalogSelectionRows.value = files
      }
      if (!systemStatus.value?.can_push) return
      const next = new Map(selectedFiles.value)
      let skipped = 0
      for (const file of selectableRows.value) {
        const key = fileKey(file)
        if (!selected) {
          next.delete(key)
        } else if (!next.has(key) && next.size < 100) {
          if (alreadySelectedDocument(next, file)) skipped += 1
          else next.set(key, file)
        } else if (!next.has(key)) {
          skipped += 1
        }
      }
      selectedFiles.value = next
      if (skipped) showNotice(`另有 ${skipped} 项因已达 100 项上限或文件版本重复而未选中，请取消部分文件或已选版本后再选择。`, 'error')
    } catch (caught) {
      const error = toError(caught)
      if (error.name !== 'AbortError' && sequence === querySequence) showNotice(`全选失败：${error.message}`, 'error')
    } finally {
      if (sequence === querySequence) {
        loading.value = false
        selectingAll.value = false
      }
    }
  }

  function catalogPath(node: CuttingCatalog) {
    const names = [node.title]
    const byId = new Map<string, CuttingCatalog>()
    const visit = (items: CuttingCatalog[]): void => items.forEach((item) => { byId.set(String(item.catalog_id), item); visit(item.children || []) })
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

  function expandCatalog(node: CuttingCatalog) {
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
      projectsLoaded.value = true
      if (!projectOptions.value.some((project) => String(project.project_id) === selectedProjectId.value)) {
        selectedProjectId.value = projectOptions.value[0] ? String(projectOptions.value[0].project_id) : ''
      }
      rememberSelectedProject(selectedProjectId.value)
      projectToOpen = projectOptions.value.find(
        (project) => String(project.project_id) === selectedProjectId.value,
      ) || null
      selectedProject.value = null
      selectedCatalog.value = null
      fileError.value = ''
      projectExpanded.value = false
      catalogTree.value = []
      expandedCatalogIds.value = new Set()
      rows.value = []
      clearSelectedFiles()
      total.value = 0
      page.value = 1
      notice.value = ''
    } catch (caught) {
      const error = toError(caught)
      if (error.name !== 'AbortError' && sequence === querySequence) showNotice(error.message, 'error')
    } finally {
      if (sequence === querySequence) projectLoading.value = false
    }
    if (projectToOpen && pageActive.value && !signal.aborted && sequence === querySequence) await selectProject(projectToOpen)
  }

  async function openSelectedProject(projectId = selectedProjectId.value) {
    const project = projectOptions.value.find((item) => String(item.project_id) === projectId)
    if (project) await selectProject(project)
  }

  async function selectProject(project: CuttingProject) {
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
    fileError.value = ''
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
    } catch (caught) {
      const error = toError(caught)
      if (error.name !== 'AbortError' && sequence === querySequence) showNotice(error.message, 'error')
    } finally {
      if (sequence === querySequence) catalogLoading.value = false
    }
  }

  function toggleProject(project: CuttingProject) {
    if (selectedProject.value?.project_id !== project.project_id || !catalogTree.value.length) {
      selectProject(project)
      return
    }
    projectExpanded.value = !projectExpanded.value
  }

  async function selectCatalog(node: CuttingCatalog) {
    selectedCatalog.value = node
    rows.value = []
    total.value = 0
    page.value = 1
    if (node.children?.length && !expandedCatalogIds.value.has(String(node.catalog_id))) expandCatalog(node)
    fileKeyword.value = ''
    appliedFileKeyword.value = ''
    clearSelectedFiles()
    await loadFiles(1)
  }

  async function applyFileSearch() {
    if (fileKeyword.value.trim() !== appliedFileKeyword.value) clearSelectedFiles()
    await loadFiles(1, fileKeyword.value.trim())
  }

  async function loadFiles(targetPage = 1, keyword = appliedFileKeyword.value) {
    selectingAll.value = false
    if (!selectedProject.value || !selectedCatalog.value) return
    const sequence = ++querySequence
    const signal = beginDirectoryRequest()
    loading.value = true
    requestedPage.value = targetPage
    fileError.value = ''
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
    } catch (caught) {
      const error = toError(caught)
      if (error.name !== 'AbortError' && sequence === querySequence) fileError.value = error.message
    } finally {
      if (sequence === querySequence) loading.value = false
    }
  }

  async function changePage(nextPage: number) {
    if (loading.value || nextPage < 1 || nextPage > totalPages.value || nextPage === page.value) return
    await loadFiles(nextPage)
  }

  async function refreshVisibleFileStatuses() {
    if (!selectedProject.value || !selectedCatalog.value || !pageActive.value) return
    const sequence = querySequence
    const data = await listCuttingFiles({
      projectId: selectedProject.value.project_id,
      catalogId: selectedCatalog.value.catalog_id,
      keyword: appliedFileKeyword.value,
      page: page.value,
      pageSize,
    }, { force: true })
    if (!pageActive.value || sequence !== querySequence) return
    const statuses = new Map(data.items.map((row) => [fileKey(row), row.dispatch_status]))
    rows.value = rows.value.map((row) => statuses.has(fileKey(row))
      ? { ...row, dispatch_status: statuses.get(fileKey(row)) }
      : row)
  }

  function pauseDirectory() {
    querySequence += 1
    selectingAll.value = false
    directoryController?.abort()
    directoryController = null
    loading.value = false
    projectLoading.value = false
    catalogLoading.value = false
  }

  return { rows, selectedFiles, selectingAll, loading, page, requestedPage, pageSize, total, projectOptions, projectSearch, projectSuggestions, selectedProjectId, projects, catalogTree, fileKeyword, appliedFileKeyword, fileError, selectedProject, selectedCatalog, projectExpanded, expandedCatalogIds, projectLoading, catalogLoading, totalPages, allCatalogFilesSelected, someCatalogFilesSelected, selectedPath, clearSelectedFiles, setFileSelected, toggleCatalogFiles, expandCatalog, loadProjects, openSelectedProject, selectProject, toggleProject, selectCatalog, applyFileSearch, loadFiles, changePage, refreshVisibleFileStatuses, projectsLoaded, pauseDirectory }
}
