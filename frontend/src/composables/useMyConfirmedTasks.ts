import type { MyConfirmedTasks } from '../api/generated/types.gen'
import { ref } from 'vue'
import { listMyConfirmedTasks } from '../api/cutting'
import { reportToday } from '../api/reportDates'
import { toError } from '../utils/errors'

export function useMyConfirmedTasks() {
  const today = reportToday()
  const period = ref({ startDate: today, endDate: today })
  const result = ref<MyConfirmedTasks | null>(null)
  const loading = ref(false)
  const error = ref('')
  const pageSize = 10
  let controller: AbortController | null = null
  let active = true

  async function load(page = result.value?.page || 1, { silent = false } = {}) {
    if (!active || (silent && loading.value)) return
    controller?.abort()
    const request = new AbortController()
    controller = request
    loading.value = true
    error.value = ''
    const query = { start_date: period.value.startDate, end_date: period.value.endDate, page, page_size: pageSize }
    try {
      let data = await listMyConfirmedTasks(query, { signal: request.signal })
      if (request.signal.aborted || controller !== request || !active) return
      const lastPage = Math.max(1, Math.ceil(data.total / pageSize))
      if (page > lastPage) data = await listMyConfirmedTasks({ ...query, page: lastPage }, { signal: request.signal })
      if (!request.signal.aborted && controller === request && active) result.value = data
    } catch (caught) {
      const exception = toError(caught)
      if (exception.name !== 'AbortError' && !request.signal.aborted && controller === request && active) error.value = exception.message
    } finally {
      if (controller === request) {
        loading.value = false
        controller = null
      }
    }
  }

  async function applyPeriod(value: { startDate: string; endDate: string }) {
    period.value = { startDate: value.startDate, endDate: value.endDate }
    result.value = null
    await load(1)
  }

  function pause() {
    active = false
    controller?.abort()
    controller = null
    loading.value = false
  }

  function activate() {
    if (active) return
    active = true
    void load()
  }

  return { period, result, loading, error, load, applyPeriod, pause, activate }
}
