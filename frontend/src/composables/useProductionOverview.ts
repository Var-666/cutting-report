import type { ProductionLine } from '../types/devices'
import type { ProductionOverview } from '../api/productionOverview.ts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getProductionOverview } from '../api/productionOverview.ts'
import { reportToday } from '../api/reportDates.ts'
import { toError } from '../utils/errors.ts'
import { REPORT_POLL_INTERVAL, REPORT_POLL_LIMIT } from '../api/reportRequests'

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
  let generation = 0
  let active = 0
  let disposed = false
  let queue: { row: OverviewRow; force: boolean; mode: 'read' | 'reload' | 'refresh' | 'poll'; run: number }[] = []
  const controllers = new Set<AbortController>()
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const polls = new Map<string, number>()
  let monthTimer: ReturnType<typeof setInterval> | undefined

  function pump() {
    while (!disposed && active < 2 && queue.length) {
      const job = queue.shift()!
      if (job.run !== generation) continue
      active += 1
      void load(job.row, job.force, job.run, job.mode).finally(() => { active -= 1; pump() })
    }
  }
  function enqueue(row: OverviewRow, force = false, mode: 'read' | 'reload' | 'refresh' | 'poll' = force ? 'poll' : 'read') {
    if (disposed || row.loading || !row.line.enabled) return
    clearTimeout(timers.get(row.line.id))
    timers.delete(row.line.id)
    row.loading = true
    row.preparing = false
    row.error = ''
    queue.push({ row, force, mode, run: generation })
    pump()
  }
  async function load(row: OverviewRow, force: boolean, run: number, mode: 'read' | 'reload' | 'refresh' | 'poll') {
    const controller = new AbortController()
    controllers.add(controller)
    try {
      const data = await getProductionOverview(row.line, month.value, controller.signal, force, () => {
        if (run === generation && !disposed) row.preparing = true
      }, mode)
      if (run !== generation || disposed) return
      row.data = data
      if (data.refreshing && !data.refreshFailed && (polls.get(row.line.id) || 0) < REPORT_POLL_LIMIT) {
        polls.set(row.line.id, (polls.get(row.line.id) || 0) + 1)
        timers.set(row.line.id, setTimeout(() => { enqueue(row, true) }, REPORT_POLL_INTERVAL))
      }
    } catch (caught) {
      const error = toError(caught)
      if (run === generation && !disposed && !controller.signal.aborted && error.name !== 'AbortError') row.error = error.message
    } finally {
      controllers.delete(controller)
      if (run === generation && !disposed) { row.loading = false; row.preparing = false }
    }
  }
  function reset() {
    generation += 1
    controllers.forEach(controller => controller.abort())
    timers.forEach(timer => clearTimeout(timer))
    timers.clear()
    polls.clear()
    queue = []
    rows.value = lines().map(line => ({ line, data: null, loading: false, preparing: false, error: '' }))
    rows.value.forEach(row => enqueue(row))
  }
  function refresh(id?: string) {
    const currentMonth = reportToday().slice(0, 7)
    if (currentMonth !== month.value) { month.value = currentMonth; return }
    rows.value.filter(row => !id || row.line.id === id).forEach(row => { polls.delete(row.line.id); enqueue(row, true, 'refresh') })
  }
  watch([month, () => JSON.stringify(lines().map(line => [line.id, line.type, line.name, line.source_key, line.enabled]))], reset, { immediate: true })
  onMounted(() => { monthTimer = setInterval(() => { month.value = reportToday().slice(0, 7) }, 60_000) })
  onBeforeUnmount(() => {
    disposed = true
    generation += 1
    queue = []
    controllers.forEach(controller => controller.abort())
    timers.forEach(timer => clearTimeout(timer))
    clearInterval(monthTimer)
  })
  return { month, rows, refresh }
}
