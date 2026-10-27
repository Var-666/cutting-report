import type { User } from '../types/auth'
import { reactive } from 'vue'
import { getEnergyDay, getEnergyMonthlyRange, getTaskEnergyDay } from '../api/energy.ts'
import { getLeadershipReport, getLatestWorkshopProductionDate, listReports, listWorkshopRangeTasks, listWorkshopTasks } from '../api/cutting.ts'
import { reportCache } from '../api/reportCache.ts'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.ts'

export const reportPreload = reactive({ identity: '', items: [] as { key: string; label: string; status: string }[] })
let generation = 0
let completion: Promise<PromiseSettledResult<void>[]> | null = null

export function stopReportPreload() {
  generation += 1
  reportCache.clear()
  reportPreload.identity = ''
  reportPreload.items = []
  completion = null
}

export function startReportPreload(
  user: User | null,
  { pathname = globalThis.location?.pathname || '/admin/home' } = {},
) {
  if (!user?.user_id || !['admin', 'worker'].includes(user.role)) {
    stopReportPreload()
    return Promise.resolve([])
  }
  const identity = `${user.role}:${user.user_id}`
  if (identity === reportPreload.identity) return completion
  // Initial warmup must share reads already started when the device became ready.
  if (reportPreload.identity) stopReportPreload()
  reportPreload.identity = identity
  const run = generation
  const today = reportToday()
  const month = today.slice(0, 7)
  const active = () => run === generation
  const jobs: { key: string; label: string; load: () => Promise<unknown> }[] = [{ key: 'workshop', label: '车间报表', load: async () => {
    const latest = await getLatestWorkshopProductionDate(today)
    if (!active()) return
    const reportDate = latest.report_date || today
    return user.role === 'worker'
      ? listWorkshopTasks({ reportDate, sortMode: 'worker' }, { warmup: true })
      : listWorkshopRangeTasks({ startDate: reportDate, endDate: reportDate })
  } }]
  if (user.role === 'admin') {
    jobs.unshift({ key: 'leadership', label: '领导报表', load: () => getLeadershipReport(month) })
    jobs.push({ key: 'equipment', label: '设备报表', load: async () => {
      const latest = await getLatestWorkshopProductionDate(today)
      if (!active()) return
      const reportDate = latest.report_date || today
      await Promise.all([getEnergyDay(reportDate), getTaskEnergyDay(reportDate)])
      if (active()) return getEnergyMonthlyRange(recentTwelveMonthStart(reportDate), reportDate)
    } })
    jobs.push({ key: 'history', label: '生成与历史', load: () => listReports({ page: 1, pageSize: 10 }) })
  }
  const routeKeys = user.role === 'worker'
    ? new Set(['workshop'])
    : pathname === '/admin/home'
      ? new Set(['leadership', 'history'])
      : pathname.startsWith('/admin/reports/leadership')
        ? new Set(['leadership'])
        : pathname.startsWith('/admin/reports/workshop')
          ? new Set(['workshop'])
          : pathname.startsWith('/admin/reports/equipment')
            ? new Set(['equipment'])
            : pathname.startsWith('/admin/reports/center')
              ? new Set(['history'])
              : new Set()
  const selectedJobs = jobs.filter((job) => routeKeys.has(job.key))
  reportPreload.items = selectedJobs.map(({ key, label }) => ({ key, label, status: 'loading' }))
  // Only warm the current page. Run jobs serially so a refresh never creates a
  // burst of unrelated production, energy and file-database requests.
  completion = (async () => {
    const results: PromiseSettledResult<void>[] = []
    for (const job of selectedJobs) {
      if (!active()) break
      try {
        await job.load()
        if (active()) reportPreload.items.find((item) => item.key === job.key)!.status = 'ready'
        results.push({ status: 'fulfilled', value: undefined })
      } catch (error) {
        if (active()) reportPreload.items.find((item) => item.key === job.key)!.status = 'failed'
        results.push({ status: 'rejected', reason: error })
      }
    }
    return results
  })()
  return completion
}
