import { reactive } from 'vue'
import {
  getEnergyRange, getLeadershipReport, getLatestWorkshopProductionDate,
  listReports, listWorkshopRangeTasks, listWorkshopTasks,
} from '../api/reports.js'
import { reportCache } from '../api/reportCache.js'
import { recentTwelveMonthStart, reportToday } from '../api/reportDates.js'

export const reportPreload = reactive({ identity: '', items: [] })
let generation = 0
let completion = null

export function stopReportPreload() {
  generation += 1
  reportCache.clear()
  reportPreload.identity = ''
  reportPreload.items = []
  completion = null
}

export function startReportPreload(user) {
  if (!user?.user_id || !['admin', 'worker'].includes(user.role)) {
    stopReportPreload()
    return Promise.resolve([])
  }
  const identity = `${user.role}:${user.user_id}`
  if (identity === reportPreload.identity) return completion
  stopReportPreload()
  reportPreload.identity = identity
  const run = generation
  const today = reportToday()
  const month = today.slice(0, 7)
  const active = () => run === generation
  const jobs = [{ key: 'workshop', label: '车间报表', load: async () => {
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
      return Promise.all([
        getEnergyRange(reportDate, reportDate),
        getEnergyRange(recentTwelveMonthStart(reportDate), reportDate),
      ])
    } })
    jobs.push({ key: 'history', label: '生成与历史', load: () => listReports({ page: 1, pageSize: 10 }) })
  }
  reportPreload.items = jobs.map(({ key, label }) => ({ key, label, status: 'loading' }))
  // Failures are isolated: a slow/unavailable data source never blocks login.
  completion = Promise.allSettled(jobs.map(async (job) => {
    try {
      await job.load()
      if (active()) reportPreload.items.find((item) => item.key === job.key).status = 'ready'
    } catch (error) {
      if (active()) reportPreload.items.find((item) => item.key === job.key).status = 'failed'
      throw error
    }
  }))
  return completion
}
