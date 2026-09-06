import { reactive } from 'vue'
import {
  getLeadershipReport, getLatestWorkshopProductionDate, listWorkshopTasks,
  getEnergyDay, getTaskEnergyDay, getEnergyMonth, listReports,
} from '../api/reports.js'
import { reportCache } from '../api/reportCache.js'
import { reportToday } from '../api/reportDates.js'

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
    return listWorkshopTasks({ reportDate: latest.report_date || today,
      sortMode: user.role === 'worker' ? 'worker' : 'manager' }, { warmup: true })
  } }]
  if (user.role === 'admin') {
    jobs.unshift({ key: 'leadership', label: '领导报表', load: () => getLeadershipReport(month) })
    jobs.push({ key: 'equipment', label: '设备报表', load: async () => {
      await Promise.all([getEnergyDay(today), getTaskEnergyDay(today)])
      if (active()) return getEnergyMonth(month)
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
