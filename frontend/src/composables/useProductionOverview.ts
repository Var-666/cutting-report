import { reportAsyncError } from '../api/browserObservations.ts'
import type { ProductionLine } from '../types/devices'
import type { ProductionOverview } from '../api/productionOverview.ts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getProductionOverview } from '../api/productionOverview.ts'
import { reportToday } from '../api/reportDates.ts'
import { toError } from '../utils/errors.ts'
import { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT, type ReportReadMode } from '../api/reportRequests'

export interface OverviewRow {
  line: ProductionLine
  data: ProductionOverview | null
  loading: boolean
  preparing: boolean
  error: string
}

export function useProductionOverview(lines: () => ProductionLine[]) {
  const month = ref(reportToday().slice(0, 7))
  const rows = ref<OverviewRow[]>([])
  const refreshingAll = ref(false)
  let active = 0
  let disposed = false
  let queue: { row: OverviewRow; mode: ReportReadMode }[] = []
  const controllers = new Map<OverviewRow, AbortController>()
  const timers = new Map<OverviewRow, ReturnType<typeof setTimeout>>()
  const polls = new Map<OverviewRow, number>()
  let monthTimer: ReturnType<typeof setInterval> | undefined

  function current(row: OverviewRow, requestedMonth = month.value) {
    return !disposed && requestedMonth === month.value && rows.value.includes(row)
  }
  function pump() {
    while (!disposed && active < 2 && queue.length) {
      const job = queue.shift()!
      if (!current(job.row)) continue
      active += 1
      load(job.row, job.mode).finally(() => { active -= 1; pump() }).catch(reportAsyncError)
    }
    if (!disposed && !active && !queue.length) refreshingAll.value = false
  }
  function enqueue(row: OverviewRow, mode: ReportReadMode = 'read') {
    if (!current(row) || row.loading || !row.line.enabled) return
    clearTimeout(timers.get(row))
    timers.delete(row)
    if (mode !== 'poll') polls.delete(row)
    row.loading = true
    row.error = ''
    queue.push({ row, mode })
    pump()
  }
  async function load(row: OverviewRow, mode: ReportReadMode) {
    const requestedMonth = month.value
    const controller = new AbortController()
    controllers.set(row, controller)
    try {
      const data = await getProductionOverview(row.line, requestedMonth, controller.signal, mode !== 'read', () => {
        if (current(row, requestedMonth)) row.preparing = true
      }, mode)
      if (!current(row, requestedMonth)) return
      row.preparing = data.preparing && data.refreshing
      if (!data.preparing) row.data = data
      if (data.preparing && !row.preparing) row.error = '后台更新未完成，请重试。'
      if (data.refreshing) {
        const attempts = polls.get(row) ?? 0
        if (attempts >= REPORT_POLL_LIMIT) row.error = '已停止自动检查，请重试。'
        else {
          polls.set(row, attempts + 1)
          timers.set(row, setTimeout(() => { enqueue(row, 'poll') }, REPORT_POLL_INTERVAL))
        }
      }
    } catch (caught) {
      const error = toError(caught)
      if (current(row, requestedMonth) && error.name !== 'AbortError') row.error = error.message
    } finally {
      controllers.delete(row)
      if (current(row, requestedMonth)) {
        row.loading = false
        if (row.error) row.preparing = false
      }
    }
  }
  function stop(row: OverviewRow) {
    controllers.get(row)?.abort()
    clearTimeout(timers.get(row))
    timers.delete(row)
    polls.delete(row)
  }
  function refresh(id?: string) {
    if (!id && refreshingAll.value) return
    const currentMonth = reportToday().slice(0, 7)
    if (currentMonth !== month.value) { month.value = currentMonth; return }
    const selected = rows.value.filter(row => (!id || row.line.id === id) && row.line.enabled && !row.loading)
    if (!id && selected.length) refreshingAll.value = true
    selected.forEach(row => enqueue(row, 'refresh'))
  }
  watch([month, () => lines().map(line => ({ ...line }))], ([period, catalog], [previousPeriod]) => {
    const previous = new Map(rows.value.map(row => [row.line.id, row]))
    rows.value = catalog.map(line => {
      const row = previous.get(line.id)
      if (period !== previousPeriod || !row || row.line.type !== line.type || row.line.source_key !== line.source_key || row.line.enabled !== line.enabled) {
        return { line, data: null, loading: false, preparing: false, error: '' }
      }
      row.line = line
      return row
    })
    for (const row of previous.values()) if (!current(row)) stop(row)
    queue = queue.filter(job => current(job.row))
    const existing = new Set(previous.values())
    rows.value.filter(row => !existing.has(row)).forEach(row => enqueue(row, period === previousPeriod && previous.has(row.line.id) ? 'reload' : 'read'))
  }, { immediate: true })
  onMounted(() => { monthTimer = setInterval(() => { month.value = reportToday().slice(0, 7) }, 60_000) })
  onBeforeUnmount(() => {
    disposed = true
    queue = []
    rows.value.forEach(stop)
    clearInterval(monthTimer)
  })
  return { month, rows, refresh, refreshingAll }
}
