// Temporary preview data for the real application entry; no production requests.
import { ref } from 'vue'
import { mockCuttingRequest } from './.cutting-preview-mock'
import { setDeviceCatalog } from './src/api/deviceContext'
import { reportToday } from './src/api/reportDates'
const today = reportToday(), scenario = ref('normal')
const date = (offset: number) => new Date(Date.parse(`${today}T00:00:00Z`) - offset * 86400000).toISOString().slice(0, 10)
const catalog = { default_device_id: 'welding', items: [{ id: 'Cutter01', type: 'cutting', name: '20KW切割机', enabled: true }, { id: 'Cutter02', type: 'cutting', name: '30KW切割机', enabled: true }, { id: 'welding', type: 'welding', name: '小组立焊接线', enabled: true }] }
setDeviceCatalog(catalog)
const processLabels = ['平焊包角', '平焊', '立焊包角', '立焊', '未知工艺']
const jobs = Array.from({ length: 154 }, (_, i) => {
  const day = date(Math.floor(i / 22)), length = 9.5 + (i * 7 % 43), hours = .18 + (i % 8) * .035
  return { id: i + 1, serial_number: `DEMO-W${String(i % 24 + 1).padStart(4, '0')}`, device_no: i % 3 + 1, robot_no: Math.floor(i / 3) % 2 + 1, riser_no: i % 4 + 1, welding_seam_no: i % 7 + 1, start_time: `${day} ${String(8 + i % 8).padStart(2, '0')}:10:00`, end_time: `${day} ${String(8 + i % 8).padStart(2, '0')}:28:00`, weld_length_m: length, working_hours: hours, weld_speed: Math.round(length * 1000 / (hours * 60)), weld_type: i % 19 === 0 ? 4 : i % 4, state: i % 2, state_label: `待确认（${i % 2}）`, weld_type_label: processLabels[i % 19 === 0 ? 4 : i % 4], quality_reasons: i % 27 === 0 ? ['作业用时关系需核查（模拟）'] : [] }
}).sort((a, b) => b.end_time.localeCompare(a.end_time))
const parts = Array.from({ length: 24 }, (_, i) => ({ id: i + 1, serial_number: `DEMO-W${String(i + 1).padStart(4, '0')}`, project_no: `DEMO-H${i % 3 + 101}`, project_name: ['小组立总段', '甲板分段', '舱壁单元'][i % 3], section_no: `B${i % 4 + 10}`, part_no: `P-${String(i + 1).padStart(3, '0')}`, device_no: i % 3 + 1, start_time: `${date(6)} 08:00:00`, end_time: `${today} 16:00:00`, theoretical_length_m: 80 + i * 4.5, actual_length_m: 74 + i * 4.2, working_hours: 1.2, weld_rate: null, arc_rate: null, state_label: `待确认（${i % 2}）`, quality_reasons: i === 0 ? ['流水号归属存在歧义（模拟）'] : [] }))
const sum = (rows: typeof jobs) => {
  const length = rows.reduce((v, r) => v + r.weld_length_m, 0), hours = rows.reduce((v, r) => v + r.working_hours, 0)
  const process = (type: number) => rows.filter(r => r.weld_type === type).reduce((v, r) => v + r.weld_length_m, 0)
  return { source_rows: rows.length, job_count: rows.length, involved_part_count: new Set(rows.map(r => r.serial_number)).size, weld_length_m: length, working_hours: hours, output_per_hour: hours ? length / hours : null, device_count: new Set(rows.map(r => r.device_no)).size, robot_station_count: new Set(rows.map(r => `${r.device_no}:${r.robot_no}`)).size, flat_wrap_length_m: process(0), flat_length_m: process(1), vertical_wrap_length_m: process(2), vertical_length_m: process(3), unknown_length_m: process(4), wrap_length_m: process(0) + process(2), invalid_time_rows: 0, invalid_serial_rows: 0, invalid_length_rows: 0, invalid_duration_rows: rows.filter(r => r.quality_reasons.length).length, unverified_state_rows: rows.length, completed_part_count: null, arc_rate: null, weld_rate: null, additional_wrap_length_m: null }
}
console.assert(sum(jobs).job_count === 154 && sum(jobs).robot_station_count === 6, 'preview fixture invariant')
const ok = (data: unknown) => Response.json({ success: true, data })
const selectJobs = (query: Record<string, string>) => jobs.filter(r => r.end_time.slice(0, 10) >= (query.start || today) && r.end_time.slice(0, 10) <= (query.end || today) && (!query.device_no || r.device_no === Number(query.device_no)) && (!query.robot_no || r.robot_no === Number(query.robot_no)) && (!query.serial_number || r.serial_number === query.serial_number) && (!query.weld_type || r.weld_type === Number(query.weld_type)) && (!query.state || r.state === Number(query.state)) && (query.anomalies_only !== 'true' || r.quality_reasons.length))
window.fetch = async (input, options = {}) => {
  const url = new URL(String(input), location.origin), params = url.searchParams
  if (!url.pathname.startsWith('/api/')) throw new Error('模拟预览禁止外部请求')
  if (url.pathname.includes('/cancel')) return ok({})
  if (url.pathname === '/api/v1/auth/status') return ok({ setup_required: false, worker_database_auth_enabled: false, user: { user_id: 'preview-admin', username: 'preview', display_name: '模拟预览', role: 'admin', active: true, auth_source: 'local' } })
  if (url.pathname === '/api/v1/production-lines' || url.pathname === '/api/v1/devices') return ok(catalog)
  if (url.pathname === '/api/v1/health') return ok({ status: 'ok', background_ready: true, background_status: 'ready', workers: {}, version: '模拟预览', demo_mode: true, data_source_mode: 'demo', data_source_name: '焊接模拟数据' })
  const cutting = mockCuttingRequest(url, options.method || 'GET')
  if (cutting) return cutting
  if (!url.pathname.startsWith('/api/v1/welding/')) return Response.json({ detail: '本次模拟预览仅提供焊接生产数据，未连接真实服务。' }, { status: 501 })

  const previewDelay = Math.min(5000, Number(new URLSearchParams(location.search).get('preview_delay')) || 0)
  if (previewDelay) await new Promise(resolve => setTimeout(resolve, previewDelay))
  if (scenario.value === 'error') return Response.json({ detail: '模拟源库连接失败，可切回正常数据后重试' }, { status: 503 })
  if (scenario.value === 'loading') await new Promise<void>((resolve, reject) => { const timeout = setTimeout(resolve, 4500); options.signal?.addEventListener('abort', () => { clearTimeout(timeout); reject(new DOMException('已取消', 'AbortError')) }, { once: true }) })
  if (url.pathname.endsWith('/analysis')) {
    const query = JSON.parse(String(options.body || '{}')).query || {}
    const metrics = sum(selectJobs(query))
    return ok({ status: 'completed', cached: false, generated_at: new Date().toISOString(), facts: [
      { id: 'F001', label: '焊接作业次数', value: `${metrics.job_count} 次`, source: '模拟作业记录' },
      { id: 'F002', label: '焊接总长度', value: `${metrics.weld_length_m.toFixed(1)} m`, source: '模拟作业记录' },
      { id: 'F003', label: '参与工件数', value: `${metrics.involved_part_count} 件`, source: '模拟流水号去重' },
      { id: 'F004', label: '累计作业用时', value: `${metrics.working_hours.toFixed(1)} h`, source: '模拟作业记录' },
    ], overview: '【模拟分析】当前区间焊接产出连续，三个设备均有作业记录。', features: '平焊与立焊共同贡献产出，包角工艺和未知工艺独立统计。', alerts: '少量作业用时和流水号关联需要人工核查，不代表设备故障。', suggestions: '结合设备及机器人筛选查看明细，优先核对异常候选记录。' })
  }
  if (url.pathname.endsWith('/export')) {
    if (params.get('format') === 'pdf') return Response.json({ detail: '模拟预览不调用真实 PDF 渲染服务，请预览 CSV 或 HTML 导出。' }, { status: 501 })
    return new Response(params.get('format') === 'html' ? '<!doctype html><meta charset="UTF-8"><h1>模拟焊接报表</h1><p>仅用于页面预览，无生产数据。</p>' : '\ufeff类型,说明\n模拟预览,无生产数据\n', { headers: { 'Content-Type': params.get('format') === 'html' ? 'text/html;charset=utf-8' : 'text/csv;charset=utf-8' } })
  }
  if (/\/parts\/\d+$/.test(url.pathname)) {
    const part = parts.find(p => p.id === Number(url.pathname.split('/').pop())) || parts[0], ambiguous = part.id === 1
    const seams = jobs.filter(r => r.serial_number === part.serial_number).map(r => ({ ...r, theoretical_length_m: r.weld_length_m + 1, actual_length_m: r.weld_length_m }))
    return ok({ part, items: ambiguous ? [] : seams, total: ambiguous ? 0 : seams.length, association_status: ambiguous ? 'pending_verification' : 'exact_serial_match', notice: ambiguous ? '模拟歧义关联：暂不展示焊缝，不允许导出。' : '模拟数据按完整流水号关联。' })
  }
  const dashboard = url.pathname.endsWith('/dashboard'), kind = dashboard ? 'jobs' : url.pathname.includes('daily') ? 'daily' : url.pathname.includes('parts') ? 'parts' : url.pathname.includes('equipment') ? 'equipment' : 'jobs'
  const query = Object.fromEntries([...params].filter(([key]) => key !== 'refresh'))
  const rows = scenario.value === 'empty' ? [] : selectJobs(query)
  const involved = new Set(rows.map(r => r.serial_number))
  const scopedParts = parts.filter(p => involved.has(p.serial_number) && (!query.project_no || p.project_no === query.project_no) && (!query.section_no || p.section_no === query.section_no) && (!query.part_no || p.part_no === query.part_no))
  const summary = { ...sum(rows), part_row_count: scopedParts.length, part_actual_length_m: scopedParts.some(p => p.id === 1) ? null : scopedParts.reduce((v, p) => v + p.actual_length_m, 0) }
  const daily = [...new Set(rows.map(r => r.end_time.slice(0, 10)))].sort().map(day => ({ ...sum(rows.filter(r => r.end_time.startsWith(day))), production_date: day }))
  const devices = [...new Set(rows.map(r => r.device_no))].sort().map(device => ({ ...sum(rows.filter(r => r.device_no === device)), device_no: device }))
  const equipment = devices.flatMap(d => [1, 2].filter(robot => rows.some(r => r.device_no === d.device_no && r.robot_no === robot)).map(robot => ({ ...sum(rows.filter(r => r.device_no === d.device_no && r.robot_no === robot)), device_no: d.device_no, robot_no: robot })))
  const projects = [...new Set(scopedParts.map(p => p.project_no))].map(project => { const ps = scopedParts.filter(p => p.project_no === project && p.id !== 1); return { project_no: project, project_name: ps[0]?.project_name || project, part_count: ps.length, cumulative_actual_length_m: ps.reduce((v, p) => v + p.actual_length_m, 0) } })
  let items: any[] = kind === 'daily' ? daily : kind === 'equipment' ? equipment : kind === 'parts' ? scopedParts : rows
  const by = query.sort_by === 'length' ? (kind === 'parts' ? 'actual_length_m' : 'weld_length_m') : kind === 'daily' ? 'production_date' : kind === 'equipment' ? 'device_no' : 'end_time'
  items = [...items].sort((a, b) => (a[by] > b[by] ? 1 : a[by] < b[by] ? -1 : 0) * (query.descending === 'false' ? 1 : -1))
  const page = Number(query.page || 1), size = dashboard ? 10 : Number(query.page_size || 20), timestamp = new Date().toISOString()
  return ok({ report_type: kind, query: { ...query, page, page_size: size }, summary, today: dashboard ? sum(rows.filter(r => r.end_time.startsWith(today))) : null, daily, device_distribution: devices, equipment, projects, projects_truncated: false, speed_distribution: [], items: items.slice((page - 1) * size, page * size), total: items.length, data_version: JSON.stringify(query), meta: { calculated_at: timestamp, data_updated_at: { weld_info_table: timestamp }, devices: [1, 2, 3], quality: summary, pending_metrics: summary.part_actual_length_m === null ? { part_actual_length_m: '模拟歧义归属，累计长度待核实' } : {}, unlinked_part_count: 0, ambiguous_part_count: scopedParts.some(p => p.id === 1) ? 1 : 0, notices: ['当前全部为隔离模拟数据，不连接生产库。', '按作业结束时间统计，工件累计值不作为区间新增产出。', '原始状态含义待确认，数据核查不代表设备故障。'], report_data: { data_as_of: timestamp, source: 'snapshot', stale: false, refreshing: false, refresh_failed: false } } })
}
