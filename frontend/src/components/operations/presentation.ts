import type { Incident } from '../../api/generated/types.gen'
import type { LogQuery } from '../../api/operations'

export function incidentLogQuery(item: Incident): LogQuery {
  const padding = 300_000
  const latest = Date.parse(item.recovered_at ?? item.last_seen_at)
  const end = latest + padding
  const start = Math.max(Date.parse(item.first_seen_at) - padding, end - 90 * 86400_000)
  const { related_kind: kind, report_id, task_id, analysis_id, request_id } = item.context
  let correlation: LogQuery
  if (item.resource_id && ['database-monitor', 'dispatch', 'email'].includes(kind)) {
    correlation = { resource_id: item.resource_id }
  } else if (report_id) correlation = { report_id }
  else if (task_id) correlation = { task_id }
  else if (analysis_id) correlation = { analysis_id }
  else if (request_id) correlation = { request_id }
  else if (item.component === 'health') correlation = { role: item.role, instance_id: item.instance_id }
  else correlation = { role: item.role, component: item.component, line_id: item.line_id,
    source_id: item.source_id, resource_id: item.resource_id, error_code: item.error_code }
  return { ...correlation, start: new Date(start).toISOString(), end: new Date(end).toISOString() }
}

export const incidentStatus = { open: '待处理', acknowledged: '已确认', recovered: '已恢复' }
export const roles = { web: 'Web', scheduler: 'Scheduler', worker: 'Consumer', automation: 'Automation' }
export const health = {
  healthy: '正常', abnormal: '异常', unknown: '未知或数据过期', paused: '已暂停',
  suspect: '连续检测中', down: '端口不可达', recovering: '正在验证恢复',
}
export const workGroup = { background: '后台任务', reports: '报表', automation: '自动化', email: '邮件' }
export const workStatus: Record<string, string> = {
  pending: '待处理', deferred: '等待重试', running: '执行中', blocked: '阻塞', failed: '失败',
  querying: '查询中', calculating: '计算中', analyzing: '分析中', rendering: '渲染中',
  retry_wait: '等待重试', unknown: '结果待核对', sending: '发送中', sent: '已发送', completed: '已完成',
}
const format = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
  hour12: false,
})
export const stamp = (value?: string | number | null) =>
  value == null ? '—' : format.format(new Date(typeof value === 'number' ? value * 1000 : value))
export const tone = (level: string) =>
  level === 'ERROR' || level === 'CRITICAL' ? 'error' : level === 'WARNING' ? 'warning' : 'default'
export const links = [
  { name: 'database-monitors', label: '数据库监控' },
  { name: 'configuration-diagnostics', label: '配置诊断' },
  { name: 'model-usage', label: '模型用量' },
  { name: 'automation', label: '报表推送与邮件记录' },
]
