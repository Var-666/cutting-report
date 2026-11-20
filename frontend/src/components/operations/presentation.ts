import type { Incident } from '../../api/generated/types.gen'
import type { LogQuery } from '../../api/operations'

export function incidentLogQuery(item: Incident): LogQuery {
  const padding = 300_000
  const latest = Date.parse(item.recovered_at ?? item.last_seen_at)
  const end = latest + padding
  const start = Math.max(Date.parse(item.first_seen_at) - padding, end - 90 * 86400_000)
  const { related_kind: kind, report_id, task_id, analysis_id, request_id } = item.context
  let correlation: LogQuery
  if (item.resource_id && item.error_code.endsWith('_DATA_QUALITY')) {
    correlation = { resource_id: item.resource_id, line_id: item.line_id, source_id: item.source_id, error_code: item.error_code }
  } else if (item.resource_id && ['database-monitor', 'dispatch', 'email'].includes(kind)) {
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
export const incidentLevels = { DEBUG: '调试', INFO: '提示', WARNING: '警告', ERROR: '错误', CRITICAL: '严重错误' }
export const operationStages: Record<string, string> = {
  archive: '核对归档文件', smtp: '提交 SMTP', persisting: '保存投递结果', recovery: '恢复任务',
  workshop: '车间生产统计', equipment: '设备统计', leadership: '领导报表',
  current_refresh: '生产统计刷新', leadership_refresh: '领导报表刷新', report_snapshot_refresh: '统计数据更新',
  energy_tasks: '任务能耗统计', energy_day: '每日能耗统计', energy_sync: '历史能耗同步',
  analysis_workshop: '车间智能分析', analysis_equipment: '设备智能分析', analysis_leadership: '领导报表分析',
  querying: '查询数据', calculating: '统计计算', analyzing: '智能分析', rendering: '生成报告',
}

export function incidentExplanation(code: string | null | undefined, message: string) {
  switch (code) {
    case 'ENERGY_TASK_DATA_QUALITY':
    case 'ENERGY_DAY_DATA_QUALITY':
      return { meaning: '部分统计范围没有取得完整、可用的电耗或气耗读数。',
        impact: '能耗合计只覆盖有效记录，缺失值不按零计算，暂不能据此评价整体能效；生产任务和工时仍按原规则统计。',
        action: '核对受影响日期的仪表绑定、采样记录及任务起止时间，确认数据补齐后重新同步能耗。' }
    case 'REPORT_SNAPSHOT_DATA_QUALITY':
      return { meaning: '报表正在使用之前保存的统计结果，最新数据尚未成功更新。',
        impact: '页面显示的是标注截至时间的结果，可能尚未包含之后的生产变化。',
        action: '先检查关联刷新任务和数据源连接，再重新刷新该报表；核对更新时间是否推进。' }
    case 'ANALYSIS_DATA_QUALITY':
      return { meaning: '智能分析未通过数据或结论校验。',
        impact: '本次分析不能作为新的有效结论，页面可能保留此前成功的分析。',
        action: '核对本次统计范围和输入数据后重新生成分析；持续失败时展开技术详情并检索相关日志。' }
    case 'DATABASE_TCP_DOWN':
    case 'POSTGRES_UNAVAILABLE':
      return { meaning: message, impact: code === 'POSTGRES_UNAVAILABLE' ? '系统状态与任务记录暂时无法可靠读取或更新。' : '依赖该数据源的查询和刷新可能失败，现有缓存不代表连接已恢复。',
        action: '检查对应服务器、网络和数据库服务，再运行连接检测；以重新检测结果确认恢复。' }
    case 'CONFIGURATION_LOAD_FAILED':
    case 'CONFIGURATION_INVALID':
      return { meaning: '最新保存的配置未能通过校验或加载。', impact: '相关进程可能仍使用上次有效配置，新设置尚未生效。',
        action: '打开配置诊断核对失败项，修正并保存配置，再确认各运行角色的加载状态。' }
    case 'CONSUMER_CAPACITY':
    case 'PROCESS_UNHEALTHY':
    case 'EXECUTION_LEASE_LOST':
      return { meaning: message, impact: '后台处理能力或任务执行状态异常，可能延迟统计刷新、报表生成及后续处理。',
        action: '先查看运行状态中的异常角色和队列，再结合关联任务排查；不要重复提交同一任务。' }
    case 'TASK_FAILED':
    case 'TASK_REPEATED_FAILURE':
    case 'TASK_BLOCKED':
    case 'REPORT_GENERATION_FAILED':
    case 'AUTOMATION_RUN_FAILED':
      return { meaning: message, impact: '关联任务未正常完成，相关统计、报告或计划结果可能尚未更新。',
        action: '检查下方关联处理状态，结合相关日志修复失败原因后，再按该业务的重试入口处理。' }
    case 'EMAIL_DELIVERY_FAILED':
    case 'EMAIL_DELIVERY_RETRY':
    case 'EMAIL_RESULT_UNKNOWN':
      return { meaning: message, impact: '关联邮件可能尚未送达；结果未知时不能直接认定未发送。',
        action: '查看邮件发送记录并核对收件结果；结果未知时先核实，避免盲目重发。' }
    default:
      return { meaning: message, impact: '具体影响需结合受影响范围和关联业务状态核实。',
        action: '先核对下方业务状态；需要维护人员协助时，可展开技术详情并检索相关日志。' }
  }
}
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
