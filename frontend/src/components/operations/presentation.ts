import type { Incident, LogRecord } from '../../api/generated/types.gen'
import type { LogQuery } from '../../api/operations'
import { formatChineseDateTime } from '../../utils/dateDisplay'
import { formatDeviceDisplay } from '../../utils/deviceDisplay'

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
  'workshop:source': '车间报表', 'workshop:statistics': '车间统计',
  'energy:day': '日能耗', 'energy:month': '月能耗', 'energy:tasks': '任务能耗',
  'energy:range': '区间能耗', 'energy:monthly-range': '月度能耗对比',
  dashboard: '焊接产线概览', 'page:daily': '焊接生产统计', 'page:equipment': '焊接设备效能',
  'page:jobs': '焊接作业报表', 'page:parts': '工件生产报表',
  database_monitor: '数据库监控', retention: '历史记录清理', download_cleanup: '临时文件清理',
  'leadership-report': '领导报表', 'equipment-report': '设备报表', 'energy-report': '能耗报表',
  'pdm-statistics': 'CSS资料统计', welding: '焊接报表', cutting: '文件派发',
  automation: '定时报表与邮件', analysis: '智能分析', reports: '报表生成与历史',
  auth: '登录与账号', database: '数据源设置', configuration: '系统配置',
}

const httpFeatures: Record<string, string> = {
  analyze_report: '智能分析', get_latest_analysis: '历史分析', get_analysis_version: '分析数据版本',
  get_leadership_report: '领导报表', get_equipment_report: '设备报表',
  get_task_energy_day: '任务能耗', get_energy_day: '日能耗', get_energy_month: '月能耗',
  get_energy_monthly_range: '月度能耗对比', get_energy_range: '区间能耗',
}
const componentFeatures: Record<string, string> = {
  lifecycle: '进程启停', health: '运行状态', 'database-monitor': '数据库监控',
  'services.report_snapshots': '报表数据更新', 'services.analysis_execution': '智能分析',
  'services.operations_service': '系统运维', 'services.database_runtime': '数据源设置',
  'services.automation_runtime': '定时报表与邮件',
}
const relatedFeatures: Record<string, string> = {
  'database-monitor': '数据库监控', dispatch: '文件派发', email: '邮件发送',
  report: '报表生成', automation: '定时报表', background: '后台任务',
}

export function operationFeature(record: LogRecord | Incident) {
  const context = 'context' in record ? record.context : record
  const stage = context.stage || ''
  const code = record.error_code || ''
  if (code.startsWith('ANALYSIS_') || record.component === 'services.analysis_execution') {
    return operationStages[stage.startsWith('analysis_') ? stage : `analysis_${stage}`]
      || (stage === 'welding' ? '焊接智能分析' : '智能分析')
  }
  if (code.startsWith('HTTP_') || code === 'INTERNAL_ERROR') {
    return httpFeatures[record.resource_id || ''] || operationStages[stage] || '页面请求'
  }
  if (code.startsWith('BROWSER_')) return code === 'BROWSER_API' ? '页面请求' : '页面运行'
  return operationStages[stage] || relatedFeatures[context.related_kind || ''] || componentFeatures[record.component] || '未记录功能'
}

export function operationLocation(record: LogRecord | Incident, lines: readonly { id: string; name: string }[]) {
  const id = record.line_id
  if (id) {
    const formatted = formatDeviceDisplay(id)
    return lines.find(line => line.id === id)?.name || (formatted !== id ? formatted : '未登记产线')
  }
  const context = 'context' in record ? record.context : record
  if (['lifecycle', 'health', 'database-monitor'].includes(record.component) ||
      ['retention', 'download_cleanup', 'database_monitor'].includes(context.stage || '') || context.related_kind === 'database-monitor') {
    return '公共系统'
  }
  return '未记录设备'
}

const operationSources = { web: '网页服务', scheduler: '任务调度', worker: '后台处理', automation: '定时推送' }
export function operationSource(record: LogRecord | Incident) {
  return record.error_code?.startsWith('BROWSER_') ? '浏览器' : operationSources[record.role]
}
const errorMessages: Record<string, string> = {
  HTTP_UNAVAILABLE: '请求暂不可用（503）', HTTP_TIMEOUT: '请求处理超时（504）', HTTP_SERVER_ERROR: '请求处理失败',
  BROWSER_API: '页面请求失败',
}
const failureReasons: Record<string, string> = {
  AnalysisDisabled: '智能分析模型未启用', AnalysisUnavailableError: '智能分析暂不可用',
  AnalysisCapacityError: '智能分析排队超时', ReportOperationTimeout: '报表处理超时',
  LlmRequestTimeout: '模型响应超时', AnalysisInputTooLarge: '分析范围超出处理上限',
  AnalysisOutputValidationError: '模型返回未通过校验', TaskSourceUnavailable: '数据源未就绪，任务执行未完成',
}
export function operationMessage(record: LogRecord | Incident) {
  const reason = 'context' in record ? record.context.error_type : record.error_type || record.exception?.type
  return failureReasons[reason || ''] || errorMessages[record.error_code || ''] || record.message
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
    case 'ANALYSIS_FAILED':
      return { meaning: message, impact: '本次分析未生成有效结果，报表生产统计仍可单独查看。',
        action: '先核对该报表是否已有可用统计，再检查模型配置和关联日志。' }
    case 'HTTP_UNAVAILABLE':
    case 'HTTP_TIMEOUT':
    case 'HTTP_SERVER_ERROR':
    case 'INTERNAL_ERROR':
      return { meaning: message, impact: '该功能的这次请求未成功完成。',
        action: '查看同一时间和设备的相关日志，核对报表、数据源或后台任务的实际状态。' }
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
    case 'TASK_ATTEMPT_FAILED':
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
export const stamp = (value?: string | number | null) =>
  formatChineseDateTime(typeof value === 'number' ? new Date(value * 1000) : value, '—', 'Asia/Shanghai')
export const tone = (level: string) =>
  level === 'ERROR' || level === 'CRITICAL' ? 'error' : level === 'WARNING' ? 'warning' : 'default'
export const links = [
  { name: 'database-monitors', label: '数据库监控' },
  { name: 'configuration-diagnostics', label: '配置诊断' },
  { name: 'model-usage', label: '模型用量' },
  { name: 'automation', label: '报表推送与邮件记录' },
]
