import { renderComponent } from './vue-component-helpers.mjs'
import assert from 'node:assert/strict'
import { test } from 'vitest'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'
import { createPinia, setActivePinia } from 'pinia'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

const energyDataScript = readFileSync(new URL('../src/composables/useEnergyReportData.ts', import.meta.url), 'utf8')
const energyChartsScript = readFileSync(new URL('../src/composables/useEnergyReportCharts.ts', import.meta.url), 'utf8')

const leadershipDataScript = readFileSync(new URL('../src/composables/useLeadershipReportData.ts', import.meta.url), 'utf8')
const leadershipChartsScript = readFileSync(new URL('../src/composables/useLeadershipReportCharts.ts', import.meta.url), 'utf8')


test('standard reports expose consistent state recovery and truthful initial metrics', () => {
  const views = ['LeadershipReportView', 'WorkshopView', 'EnergyPeriodReportView'].map((name) => descriptor(name))
  for (const view of views) {
    assert.match(view.scriptSetup.content, /import ReportFeedback from/)
    assert.match(view.template.content, /:has-data=/)
    assert.match(view.template.content, /:loading=/)
    assert.match(view.template.content, /:empty=/)
    assert.match(view.template.content, /@retry=/)
    assert.match(view.template.content, /:aria-busy="loading"/)
  }

  assert.match(views[0].template.content, /total\.task_count \?\? '--'/)
  assert.match(views[1].template.content, /result\.report \? workshopMetrics\.taskCount : '--'/)
  assert.match(views[2].template.content, /taskDay \? \(summary\.task_count \?\? '--'\) : '--'/)
  assert.match(views[2].scriptSetup.content, /summarySubtitle, format,[\s\S]*} = useEnergyReportData\(/)
  assert.match(energyDataScript, /if \(value === null \|\| value === undefined \|\| !Number\.isFinite\(Number\(value\)\)\) return '--'/)
})

test('report export feedback exposes the recovery action in each existing page', () => {
  for (const name of ['AdminLeadershipView', 'AdminWorkshopView', 'AdminEquipmentView']) {
    const view = descriptor(name)
    assert.match(view.scriptSetup.content, /retryExport/)
    assert.match(view.template.content, /<ReportFeedback/)
    assert.match(view.template.content, /:action-label="retryLabel"/)
    assert.match(view.template.content, /@action="retryExport"/)
  }
})

test('report analysis uses server source versions and starts immediately', () => {
  const analysis = descriptor('AnalysisPanel', 'components')
  const script = analysis.scriptSetup.content
  assert.match(script, /dataVersion: \{ type: String/)
  assert.match(script, /summaryDataVersion: \{ type: String/)
  assert.match(script, /analysisDataVersion = computed/)
  assert.doesNotMatch(script, /AUTO_ANALYSIS_DEBOUNCE_MS|4000/)
  assert.doesNotMatch(script, /activationTimer\.schedule\(/)
  assert.match(script, /loadAnalysis\(false, targetKey\)/)
  assert.match(script, /function reloadAnalysis\(\)[\s\S]*loadAnalysis\(true\)/)
  assert.match(script, /onDeactivated\(pauseAnalysis\)/)
  assert.match(script, /requestController\?\.abort\(\)/)
  assert.match(script, /analyzeReport\([\s\S]*\}, \{ signal \}\)/)
  assert.match(script, /dataVersion: analysisDataVersion\.value \|\| undefined/)
  assert.doesNotMatch(script, /报表数据分析|guardrail_fallback|simulated_fallback|降级/)
  assert.match(analysis.template.content, /<ReportAnalysis[^>]*:analysis="analysis"/)
  assert.match(script, /const targetKey = requestKey\(\)[\s\S]*if \(!props\.ready \|\| !props\.reportDate\)/)
  assert.match(script, /if \(scope !== displayedScope\) \{\s*analysis\.value = null/)

  const expectedVersions = new Map([
    ['LeadershipReportView', /:summary-data-version="monthlyReport\?\.analysis_summary_source_version \|\| ''"/],
    ['WorkshopView', /:data-version="result\.analysis_source_version \|\| ''"/],
    ['EnergyPeriodReportView', /:summary-data-version="monthlyReport\?\.analysis_source_version \|\| ''"/],
  ])
  for (const [name, pattern] of expectedVersions) {
    const template = descriptor(name).template.content
    assert.match(template, pattern)
    assert.doesNotMatch(template, /context-version/)
  }

  const api = readFileSync(new URL('../src/api/cutting.ts', import.meta.url), 'utf8')
  assert.match(api, /analyzeReport\(\{[^}]*dataVersion[^}]*\}/)
  assert.match(api, /data_version: dataVersion \|\| undefined/)
  assert.match(script, /props\.summaryPeriodStart[\s\S]*props\.summaryPeriodEnd[\s\S]*props\.summaryDataVersion/)
})

test('report values use one decimal place throughout the UI', () => {
  const leadership = leadershipDataScript
  const workshop = descriptor('WorkshopView').scriptSetup.content
  const energy = energyDataScript
  const home = descriptor('AdminHomeView').scriptSetup.content
  const database = descriptor('DatabaseView').scriptSetup.content
  const analysis = descriptor('ReportAnalysis', 'components').scriptSetup.content

  assert.match(leadership, /digits = 1/)
  assert.match(leadership, /minimumFractionDigits: 1/)
  assert.match(workshop, /minimumFractionDigits: 1, maximumFractionDigits: 1/)
  assert.match(energy, /digits = 1/)
  assert.match(descriptor('AdminHomeView').template.content, /title="切割长度"[^>]*:precision="1"/)
  assert.match(database, /maximumFractionDigits: 1/)
  assert.match(analysis, /minimumFractionDigits: 1/)

  for (const source of [leadership, workshop, energy, home, database, analysis]) {
    assert.doesNotMatch(source, /(?:minimum|maximum)FractionDigits:\s*3|digits\s*=\s*3/)
  }
})

test('leadership dashboard uses verified comparison data and the home visual baseline', () => {
  const leadership = descriptor('LeadershipReportView')
  const template = leadership.template.content
  const script = leadership.scriptSetup.content
  const styles = leadership.styles.map((style) => style.content).join('\n')

  assert.match(template, /<h1>领导报表<\/h1>/)
  assert.equal((template.match(/class="leadership-kpi /g) || []).length, 4)
  assert.match(template, /<AnalysisPanel/)
  assert.doesNotMatch(template, /leadership-analysis/)
  assert.match(leadershipDataScript, /report\.value\?\.comparison_total/)
  assert.match(leadershipDataScript, /function changeRate\(current: unknown, previous: unknown\)/)
  assert.match(leadershipDataScript, /defaultMonthlyView\.value \? '较上月' : '较对比区间'/)
  assert.match(styles, /grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/)
})

test('equipment dashboard uses verified energy metrics and responsive home visuals', () => {
  const equipment = descriptor('EnergyPeriodReportView')
  const template = equipment.template.content
  const script = equipment.scriptSetup.content
  const styles = equipment.styles.map((style) => style.content).join('\n')

  assert.match(template, /aria-label="设备报表查询条件"/)
  assert.equal((template.match(/class="device-kpi /g) || []).length, 5)
  assert.match(template, /<AppIcon name="zap"/)
  assert.match(template, /<AppIcon name="wind"/)
  assert.match(template, /<AppIcon name="activity"/)
  assert.match(template, /<AppIcon name="tasks"/)
  assert.match(template, /<AppIcon name="clock"/)
  assert.match(energyDataScript, /const energyCoverage = computed/)
  assert.doesNotMatch(template, /设备利用率/)
  assert.match(styles, /grid-template-columns:repeat\(5,minmax\(0,1fr\)\)/)
})

test('workshop dashboard uses verified confirmation metrics and responsive home visuals', () => {
  const workshop = descriptor('WorkshopView')
  const structure = descriptor('WorkshopStructureCharts', 'components')
  const template = workshop.template.content
  const script = workshop.scriptSetup.content
  const styles = workshop.styles.map((style) => style.content).join('\n')
  const structureTemplate = structure.template.content
  const structureScript = structure.scriptSetup.content
  const structureStyles = structure.styles.map((style) => style.content).join('\n')

  assert.equal((template.match(/class="workshop-kpi /g) || []).length, 5)
  assert.match(template, /<AppIcon name="tasks"/)
  assert.match(template, /<AppIcon name="clock"/)
  assert.match(template, /<AppIcon name="check"/)
  assert.match(template, /<AppIcon name="briefcase"/)
  assert.match(template, /<AppIcon name="activity"/)
  assert.match(template, /<AnalysisPanel/)
  assert.match(template, /<WorkshopStructureCharts/)
  assert.match(template, /result\.duration_distribution/)
  assert.match(template, /result\.status_distribution/)
  assert.match(template, /result\.top_cut_length_tasks/)
  assert.doesNotMatch(template, /workshop-analysis/)
  assert.match(script, /import WorkshopStructureCharts from/)
  assert.match(script, /const workshopMetrics = computed/)
  assert.doesNotMatch(template, /异常任务/)
  assert.match(structureScript, /import \{ BarChart, PieChart \} from 'echarts\/charts'/)
  assert.match(structureScript, /type: 'pie'/)
  assert.match(structureScript, /type: 'bar'/)
  assert.match(structureScript, /topCutLengthTasks: \{ type: Array/)
  assert.match(structureScript, /renderMode: 'richText'/)
  assert.match(structureTemplate, /任务时长分布/)
  assert.match(structureTemplate, /任务确认状态/)
  assert.match(structureTemplate, /切割长度 Top 5/)
  assert.match(structureTemplate, /class="status-progress"/)
  assert.match(structureTemplate, /当前任务暂无有效切割长度/)
  assert.doesNotMatch(structureTemplate, /异常/)
  assert.match(structureStyles, /grid-template-columns:repeat\(3,minmax\(0,1fr\)\)/)
  assert.match(structureStyles, /@media\(max-width:1100px\)\{\.workshop-structure__grid\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/)
  assert.match(structureStyles, /@media\(max-width:760px\)\{\.workshop-structure__grid\{grid-template-columns:1fr\}/)
  assert.match(styles, /grid-template-columns:repeat\(5,minmax\(0,1fr\)\)/)
})

test('home panels expose independent loading states', () => {
  const home = descriptor('AdminHomeView')
  const equipment = descriptor('EnergyPeriodReportView')

  assert.match(home.scriptSetup.content, /const overviewLoading = ref\(true\)/)
  assert.match(home.scriptSetup.content, /const historyLoading = ref\(true\)/)
  assert.doesNotMatch(home.template.content, /报表预加载/)
})

test('home quick access retains the three report destinations with semantic theme styles', () => {
  const home = descriptor('AdminHomeView')
  for (const route of ['leadership', 'workshop', 'equipment']) assert.ok(home.scriptSetup.content.includes(`/admin/reports/${route}`))
  assert.match(home.template.content, /aria-label="核心报表快速入口"/)
  assert.match(home.template.content, /v-for="item in quickLinks"/)
  assert.match(home.template.content, /hover:bg-primary\/10/)
})

test('home KPI and production ring show verified data instead of decorative placeholders', async () => {
  const home = descriptor('AdminHomeView')
  const template = home.template.content
  const script = home.scriptSetup.content

  assert.equal((template.match(/<HomeKpiCard /g) || []).length, 3)
  assert.match(template, /grid-cols-1 sm:grid-cols-2 xl:grid-cols-4/)
  assert.match(template, /aria-label="生产天数与能耗"/)
  setActivePinia(createPinia())
  const card = await renderComponent('components/HomeKpiCard', { title: '任务数', value: 6, series: [1, 2, 3] })
  assert.equal((card.match(/<rect\b/g) || []).length, 3)
  assert.match(card, /任务数趋势，基于最近 3 个有效数据点/)
  assert.match(card, /n-badge/)
  const reportCard = await renderComponent('components/HomeKpiCard', { title: '任务数', value: 6, series: [1, 2, 3], variant: 'report' })
  assert.match(reportCard, /data-sparkline__line/)
  assert.doesNotMatch(reportCard, /<rect\b/)
  assert.match(script, /dailyRecords\.value\.map\(\(item\) => item\.cut_length_m\)/)
  assert.match(script, /Number\(previous\) <= 0\) return \{ text: '暂无上月对比'/)
  assert.match(script, /percent\}% 较上月/)
  assert.doesNotMatch(script, /上期/)
  assert.match(template, /<NNumberAnimation[^>]*:to="activeDays"/)
})

test('workshop report includes the confirmed-worker interval summary', () => {
  const workshop = descriptor('WorkshopView')
  const trendPanel = descriptor('ReportTrendPanel', 'components')
  const template = workshop.template.content
  const css = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  const trendStyles = trendPanel.styles.map((style) => style.content).join('\n')
  assert.match(template, /工人工时汇总/)
  assert.match(template, /worker\.company_no/)
  assert.match(template, /worker\.confirmed_task_count/)
  assert.match(template, /worker\.total_work_time_seconds/)
  assert.match(workshop.scriptSetup.content, /import ReportTrendPanel from '\.\.\/components\/ReportTrendPanel\.vue'/)
  assert.equal((template.match(/<ReportTrendPanel/g) || []).length, 2)
  assert.equal((template.match(/:show-chart="false"/g) || []).length, 2)
  assert.equal((template.match(/fit-table-content/g) || []).length, 2)
  assert.match(template, /v-if="!isWorker && result\.worker_summaries\?\.length"[\s\S]*class="worker-time-summary"/)
  assert.match(template, /title="工人工时汇总"/)
  assert.doesNotMatch(template, /<section class="panel workshop-panel"|class="panel worker-time-summary"/)
  assert.doesNotMatch(template, /所选区间暂无工人确认数据/)
  assert.match(template, /<AppReportTable :columns="workerTableColumns" :items="result.worker_summaries"/)
  assert.match(css, /\.panel, \.job-card\s*\{[^}]*margin-bottom: 22px;/)
  assert.doesNotMatch(css, /\.worker-time-summary\s*\{[^}]*margin:/)
  assert.match(trendStyles, /\.report-trend-panel__content--fit-table \.report-trend-panel__table\s*\{[^}]*height:auto;[^}]*max-height:none;/)
})

test('device report is one page with task details and a default monthly summary', () => {
  const container = descriptor('AdminEquipmentView')
  const energy = descriptor('EnergyPeriodReportView')

  assert.match(container.template.content, /<EnergyPeriodReportView[\s\S]*@export="exportReport"/)
  assert.doesNotMatch(container.template.content, /EquipmentReportView|reportTab|KeepAlive/)
  assert.match(energy.template.content, /<StandardReportQuery/)
  assert.doesNotMatch(energy.template.content, /报表类型|任务生产报表|change-report-tab/)
  assert.match(descriptor('StandardReportQuery', 'components').template.content, /<span><AppIcon name="chart"[^>]*\/>图表类型<\/span>/)
  assert.match(energy.template.content, /<h1>设备报表<\/h1>/)
  assert.match(energy.template.content, /:max-days="31"/)
  assert.match(energy.template.content, /任务能耗明细/)
  assert.match(energy.template.content, /#summary-c0>汇总<\/template>/)
  const taskSummary = energy.template.content.slice(energy.template.content.indexOf('#summary-c0'), energy.template.content.indexOf('#empty'))
  assert.match(taskSummary, /#summary-c2><\/template>/)
  assert.match(taskSummary, /#summary-c3><\/template>/)
  assert.doesNotMatch(taskSummary, /equipmentScopeLabel|summary\.task_count/)
  assert.match(energy.scriptSetup.content, /近12个月月度能耗汇总/)
  assert.match(energy.template.content, /aria-label="任务能耗明细表"/)
  assert.match(energy.template.content, /:aria-label="summaryTableLabel"/)
  assert.match(energy.template.content, /<AppReportTable flex-height :columns="summaryTableColumns"/)
  assert.match(energy.template.content, /#header-c1>\{\{ summaryDateHeading \}\}/)
  assert.match(energy.scriptSetup.content, /const summaryTableColumns[\s\S]*任务数[\s\S]*切割时长[\s\S]*电耗/)
  assert.match(energyDataScript, /const taskChartRows = computed\(\(\) => taskRows\.value[\s\S]*\.slice\(0, 50\)[\s\S]*\.sort\(\(left, right\) => String\(left\.production_time \|\| ''\)\.localeCompare\(String\(right\.production_time \|\| ''\)\)\)\)/)
  assert.match(energyChartsScript, /const labels = taskChartRows\.value\.map/)
  assert.match(energy.template.content, /month\?\.total_task_count/)
  assert.doesNotMatch(energy.template.content, /电耗覆盖|气耗覆盖|保存状态/)
  assert.match(energy.template.content, /#empty>\{\{ summaryLoading/)
  assert.doesNotMatch(energy.template.content, /任务结束/)
  assert.match(energy.template.content, /#empty>\{\{ loading/)
  assert.doesNotMatch(energy.template.content, /scope="row" colspan="5"/)
  assert.equal((energy.template.content.match(/#summary-c0>汇总/g) || []).length, 2)
  assert.doesNotMatch(energy.template.content, /当日任务汇总|当月汇总/)
  assert.equal((energy.template.content.match(/<AppReportTable flex-height/g) || []).length, 2)
  assert.doesNotMatch(energy.template.content, /<colgroup>/)
  assert.match(energy.scriptSetup.content, /import AnalysisPanel from '\.\.\/components\/AnalysisPanel\.vue'/)
  assert.match(energy.template.content, /<AnalysisPanel[\s\S]*report-type="equipment"/)
  assert.match(energy.template.content, /:ready="analysisReady"/)
  assert.equal((energy.template.content.match(/<ReportTrendPanel/g) || []).length, 2)
  assert.equal((energy.template.content.match(/chart-label=/g) || []).length, 2)
  assert.match(energyChartsScript, /function renderCharts\(/)
  assert.match(energyChartsScript, /taskPanel\.value\?\.getChartElement\(\)/)
  const energyStyles = energy.styles.map((style) => style.content).join('\n')
  assert.doesNotMatch(energyStyles, /\.energy-period-query\s*\{[^}]*grid-template-columns:/)
  assert.doesNotMatch(energyStyles, /\.energy-period-query\s*\{[^}]*display:/)
})


test('leadership report keeps refresh automatic and exposes only the normal query action', () => {
  const leadership = descriptor('LeadershipReportView')
  assert.doesNotMatch(leadership.template.content, /刷新最新数据|@click="refreshReport"/)
  assert.doesNotMatch(leadership.scriptSetup.content, /refreshLeadershipReport|function refreshReport/)
  assert.match(leadership.scriptSetup.content, /scheduleRefreshPoll/)
})

test('alarm center uses source timestamps and presents the latest evidenced model analysis', () => {
  const alarm = descriptor('AlarmCenterView')
  const template = alarm.template.content
  const script = alarm.scriptSetup.content
  const styles = alarm.styles.map((style) => style.content).join('\n')

  assert.match(template, /<h1>智能告警中心<\/h1>/)
  assert.match(template, /aria-label="告警监控概况"/)
  assert.match(template, /按告警级别筛选/)
  assert.match(script, /title: "告警时间"[\s\S]*title: "发现时间"/)
  assert.match(template, /formatChineseDateTime\(item\.first_start_time\)/)
  assert.match(template, /同步游标/)
  assert.match(template, /last_alarm_id/)
  assert.doesNotMatch(template, /已同步记录/)
  assert.match(script, /const startValue = item\.first_start_time/)
  assert.match(script, /item\.status === 'closed' \? item\.latest_remove_time : currentTime\.value/)
  assert.doesNotMatch(script, /item\?\.first_start_time \|\| item\?\.first_seen_at/)
  assert.doesNotMatch(script, /item\.closed_at \|\| item\.last_seen_at/)
  assert.match(script, /listAlarmIncidentPage/)
  assert.match(script, /latestModelAlarmReport\(reports\.value\)/)
  assert.match(template, /latestAiAnalysis\.summary/)
  assert.match(template, /finding\.confidence/)
  assert.match(template, /finding\.evidence_ids/)
  assert.match(template, /latestAiAnalysis\.facts/)
  assert.match(template, /latestAiAnalysis\.limitations/)
  assert.match(template, /模型结论仅用于辅助排查/)
  assert.doesNotMatch(script, /insightSummary|insightCause/)
  assert.doesNotMatch(template, /检查电源及急停回路|检查气压与冷却系统/)
  assert.match(template, /:items="filteredIncidents"[^>]*@page="changeIncidentPage"/)
  assert.match(template, /:total="incidentSummary.total"[^>]*:page="incidentPage"/)
  assert.match(script, /const reportPageSize = 10/)
  assert.match(script, /listAlarmReportPage\(\{ page: targetReportPage, pageSize: reportPageSize \}/)
  assert.match(template, /:items="reportItems"/)
  assert.match(template, /@page="changeReportPage"/)
  assert.match(template, /:total="reportSummary.total"[^>]*:page="reportPage"/)
  assert.match(template, /告警事件读取失败[\s\S]*当前告警状态未知/)
  assert.match(template, /告警报告读取失败/)
  assert.match(template, /当前设备暂无告警记录/)
  assert.match(template, /当前没有状态异常的告警/)
  assert.match(styles, /\.alarm-page>\.panel\{margin-bottom:0\}/)
  assert.match(styles, /\.alarm-content-grid>\.panel\{margin-bottom:0\}/)
  // Long model analysis must sit below the full-width table, never reserve a tall side column.
  assert.match(styles, /\.alarm-content-grid\{[^}]*grid-template-columns:minmax\(0,1fr\);[^}]*align-items:start/)
  assert.doesNotMatch(styles, /\.alarm-content-grid\{[^}]*3\.2fr/)
  assert.match(template, /class="alarm-ai-columns"/)
  assert.match(styles, /\.alarm-ai-columns\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/)
  assert.match(styles, /@media\(max-width:760px\)\{\.alarm-ai-columns\{grid-template-columns:minmax\(0,1fr\)\}/)
  assert.match(styles, /\.alarm-content-grid \.alarm-table-wrap th:last-child,[^}]*position:sticky;right:0/)
  assert.match(styles, /\.alarm-empty-state\{[^}]*min-height:116px;[^}]*padding:26px;/)
  // Incident rows must inherit the standard table scale, including nested text.
  assert.doesNotMatch(styles, /\.alarm-page \.alarm-table-wrap (?:th|td)\{font-size:/)
  assert.match(styles, /\.alarm-table-wrap:not\(\.report-table-wrap\) :is\([^}]*font-size:inherit/)
})


test('workshop confirmations support own revocation and audited admin correction', () => {
  const workshop = descriptor('WorkshopView')
  const script = readFileSync(new URL('../src/composables/useWorkshopCorrection.ts', import.meta.url), 'utf8')
  const correctionTemplate = descriptor('workshop/WorkshopCorrectionDialog', 'components').template.content
  const template = workshop.template.content
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')

  assert.match(script, /row\.confirmed_by_id === user\(\)\.user_id/)
  assert.match(script, /revokeWorkshopTask\(row\.task_id/)
  assert.match(script, /correctWorkshopTask\(row\.task_id/)
  assert.match(script, /listWorkshopConfirmationAudits/)
  assert.match(template, /撤销我的确认/)
  assert.match(template, /更正确认/)
  assert.match(correctionTemplate, /更正原因/)
  assert.match(script, /const correctionReasonPresets =/)
  assert.match(script, /workerRevoke:[\s\S]*adminRevoke:[\s\S]*adminReassign:/)
  assert.match(script, /function selectCorrectionReason\(reason: string\)/)
  assert.match(correctionTemplate, /aria-label="常用更正原因"/)
  assert.match(correctionTemplate, /v-for="preset in visibleCorrectionReasonPresets"/)
  assert.match(correctionTemplate, /选择常用原因，或直接填写其他原因/)
  assert.match(correctionTemplate, /该任务的确认记录/)
  assert.match(styles, /\.confirmation-person\s*\{[^}]*grid-template-columns:minmax\(0,1fr\) auto;[^}]*align-items:center;/)
  assert.match(styles, /\.correction-button\s*\{[^}]*white-space:nowrap!important;/)
  assert.match(styles, /\.confirmation-reason-presets button\.active\s*\{[^}]*background:rgb\(var\(--info-color\)\);/)
})

test('intelligent analysis omits the analysis boundary section', () => {
  const analysis = descriptor('ReportAnalysis', 'components')
  assert.doesNotMatch(analysis.template.content, /分析边界|analysis\.limitations|analysis-limit/)
})

test('all standard report analyses follow the selected report range', () => {
  const leadership = descriptor('LeadershipReportView')
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const analysis = descriptor('AnalysisPanel', 'components')
  const api = readFileSync(new URL('../src/api/cutting.ts', import.meta.url), 'utf8')

  assert.match(leadership.template.content, /<AnalysisPanel[\s\S]*report-type="leadership"/)
  assert.match(leadership.template.content, /:period-start="defaultMonthlyView \? '' : appliedPeriodStart"/)
  assert.match(leadership.template.content, /:period-end="defaultMonthlyView \? '' : appliedPeriodEnd"/)
  assert.match(leadership.template.content, /:summary-period-start="monthlyPeriodStart"/)
  assert.match(leadership.template.content, /:summary-period-end="monthlyPeriodEnd"/)
  assert.doesNotMatch(leadership.template.content, /<AnalysisPanel v-else|区间智能分析将在导出报告时生成/)
  assert.match(workshop.template.content, /<AnalysisPanel[\s\S]*v-if="!isWorker"[\s\S]*report-type="workshop"/)
  assert.match(workshop.template.content, /:period-start="appliedRouteState\.startDate"/)
  assert.match(workshop.template.content, /:period-end="appliedRouteState\.endDate"/)
  assert.doesNotMatch(workshop.template.content, /filters\.startDate === filters\.endDate/)
  assert.match(equipment.template.content, /<AnalysisPanel[\s\S]*report-type="equipment"/)
  assert.match(equipment.template.content, /:period-start="rangeQueryApplied \? appliedPeriodStart : ''"/)
  assert.match(equipment.template.content, /:period-end="rangeQueryApplied \? appliedPeriodEnd : ''"/)
  assert.match(equipment.template.content, /:summary-period-start="summaryPeriodStart"/)
  assert.match(equipment.template.content, /:summary-period-end="summaryPeriodEnd"/)
  assert.doesNotMatch(equipment.template.content, /<AnalysisPanel v-else|区间智能分析将在导出报告时生成/)
  assert.match(analysis.scriptSetup.content, /periodStart: props\.periodStart \|\| undefined/)
  assert.match(analysis.scriptSetup.content, /periodEnd: props\.periodEnd \|\| undefined/)
  assert.match(analysis.scriptSetup.content, /summaryPeriodStart: props\.summaryPeriodStart \|\| undefined/)
  assert.match(analysis.scriptSetup.content, /summaryPeriodEnd: props\.summaryPeriodEnd \|\| undefined/)
  assert.match(api, /period_start: periodStart/)
  assert.match(api, /period_end: periodEnd/)
  assert.match(api, /summary_period_start: summaryPeriodStart/)
  assert.match(api, /summary_period_end: summaryPeriodEnd/)
})


test('switching cached workshop and equipment reports does not force another query', () => {
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const preload = readFileSync(new URL('../src/state/reportPreload.ts', import.meta.url), 'utf8')
  const cache = readFileSync(new URL('../src/api/reportCache.ts', import.meta.url), 'utf8')

  assert.match(workshop.scriptSetup.content, /force = false/)
  assert.match(workshop.scriptSetup.content, /if \(!pageActive\.value \|\| !isWorker\.value \|\| refreshTimer\) return/)
  assert.match(workshop.scriptSetup.content, /if \(isWorker\.value && result\.value\.report/)
  assert.doesNotMatch(equipment.scriptSetup.content, /onActivated\([\s\S]{0,300}getEnergyRange/)
  assert.match(preload, /listWorkshopRangeTasks\(\{ startDate: reportDate, endDate: reportDate \}\)/)
  assert.doesNotMatch(preload, /getEnergyRange\(recentTwelveMonthStart/)
  assert.match(cache, /REPORT_VIEW_CACHE_TTL = 5 \* 60 \* 1000/)
})

test('workshop pagination does not invalidate an unchanged analysis context', () => {
  const view = descriptor('WorkshopView')
  const analysis = descriptor('AnalysisPanel', 'components').scriptSetup.content
  const workshop = view.scriptSetup.content
  const template = view.template.content
  const readyExpression = workshop.match(/const analysisReady = computed\(\(\) => Boolean\(([\s\S]*?)\n\)\)/)

  assert.ok(readyExpression)
  assert.match(readyExpression[1], /!loading\.value/)
  assert.match(analysis, /if \(scope !== displayedScope\) \{\s*analysis\.value = null/)
  assert.match(template, /:page="result.page"[^>]*:page-size="result.page_size"/)
  assert.match(template, /@page="changePage"/)
})

test('cached report resources pause on deactivation and cache is bounded', () => {
  const layout = descriptor('ReportsLayout', 'layouts')
  const leadership = descriptor('LeadershipReportView')
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const controls = ['ReportPeriodPicker']
    .map((name) => descriptor(name, 'components').scriptSetup.content)

  assert.match(layout.template.content, /<KeepAlive :max="3">/)
  for (const view of [leadership, workshop, equipment]) {
    assert.match(view.scriptSetup.content, /onDeactivated\(/)
    assert.match(view.template.content, /<AppReportTable/)
  }
  assert.match(leadership.scriptSetup.content, /disposeCharts\(\)/)
  assert.match(equipment.scriptSetup.content, /disposeCharts\(\)/)
  assert.match(leadership.scriptSetup.content, /onBeforeUnmount\(\(\) => pausePage\(true\)\)/)
  assert.match(leadershipChartsScript, /dailyChart \|\|= init\(dailyChartElement, chartTheme\.darkMode\.value \? 'dark' : undefined\)/)
  assert.match(energyChartsScript, /taskChart \|\|= init\(taskChartElement, chartTheme\.darkMode\.value \? 'dark' : undefined\)/)
  assert.doesNotMatch(leadership.scriptSetup.content, /onDeactivated\([\s\S]{0,120}disposeCharts/)
  for (const control of controls) {
    assert.match(control, /createActivationBinding/)
    assert.match(control, /onActivated\(listenerBinding\.activate\)/)
    assert.match(control, /onDeactivated\(pause\)/)
  }
})

test('workshop and report history polling is cancelled when the page leaves', () => {
  const workshop = descriptor('WorkshopView').scriptSetup.content
  const reportCenter = descriptor('ReportCenterView').scriptSetup.content
  assert.doesNotMatch(workshop, /setInterval\(/)
  assert.match(workshop, /refreshTimer = window\.setTimeout\(async \(\) =>/)
  assert.match(workshop, /await load\(result\.value\.page,[\s\S]*startRefreshTimer\(\)/)

  assert.match(reportCenter, /function stopPolling\(\)[\s\S]*pollController\?\.abort\(\)/)
  assert.match(reportCenter, /getReportStatus\(reportId, \{ signal: controller\.signal \}\)/)
  assert.match(reportCenter, /controller\.signal\.aborted \|\| sequence !== pollSequence\) return/)
  assert.match(reportCenter, /listReports\(\{ page, pageSize, force, signal: controller\.signal \}\)/)
  assert.match(reportCenter, /sequence !== historySequence/)
  assert.match(reportCenter, /onBeforeUnmount\(\(\) => \{[\s\S]*pageActive = false[\s\S]*stopPolling\(\)/)
})

test('file dispatch uses PDM references and has no saved-file prerequisite', () => {
  const dispatch = descriptor('CuttingDispatchView')

  assert.match(dispatch.template.content, /<aside class="panel directory-panel">/)
  assert.match(dispatch.template.content, /目录文件/)
  assert.match(readFileSync(new URL('../src/composables/useCuttingDispatchBatch.ts', import.meta.url), 'utf8'), /document_id: item\.documentId/)
  assert.match(readFileSync(new URL('../src/composables/useCuttingDispatchBatch.ts', import.meta.url), 'utf8'), /version_id: item\.versionId/)
  assert.match(readFileSync(new URL('../src/composables/useCuttingDispatchBatch.ts', import.meta.url), 'utf8'), /file_path: item\.filePath/)
  assert.doesNotMatch(dispatch.scriptSetup.content, /listCuttingDownloads|downloadCuttingFile/)
  assert.match(dispatch.template.content, /PDM项目目录/)
})
