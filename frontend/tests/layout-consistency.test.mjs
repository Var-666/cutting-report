import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

test('all admin pages share the home content width and navigation dimensions', () => {
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  const layout = descriptor('AdminLayout', 'layouts').template.content

  assert.match(layout, /class="topbar"/)
  assert.match(layout, /<div class="app-shell">/)
  assert.match(styles, /\.topbar \{[^}]*height:70px;[^}]*1520px/)
  assert.match(styles, /\.app-shell\{background:#f3f6f9\}/)
  assert.match(styles, /\.app-shell \.admin-main\{width:min\(1520px,calc\(100% - 48px\)\);padding-top:16px\}/)
  assert.match(styles, /@media\(max-width:560px\)\{\.app-shell \.admin-main\{width:calc\(100% - 24px\)\}/)
  assert.doesNotMatch(styles, /app-shell--home/)
})

test('first page cards clear the header without doubling their own top spacing', () => {
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  const home = readFileSync(new URL('../src/assets/admin-home.css', import.meta.url), 'utf8')
  const dispatch = descriptor('CuttingDispatchView').styles.map((style) => style.content).join('\n')

  assert.match(styles, /\.worker-main \{ padding:16px 0 24px; \}/)
  assert.match(styles, /\.report-navigation \{[^}]*margin:0;/)
  assert.match(home, /\.command-home \{[^}]*padding: 0 0 24px;/)
  assert.doesNotMatch(home, /\.command-home \{ gap: 12px; padding-top:/)
  assert.match(dispatch, /\.dispatch-flow \{[^}]*margin:12px 0 0;/)
})

test('the supplied project logo is reused across primary product entry points', () => {
  const admin = descriptor('AdminLayout', 'layouts').template.content
  const worker = descriptor('WorkerLayout', 'layouts').template.content
  const login = descriptor('LoginView').template.content
  const index = readFileSync(new URL('../index.html', import.meta.url), 'utf8')

  for (const view of [descriptor('AdminLayout', 'layouts'), descriptor('WorkerLayout', 'layouts'), descriptor('LoginView')]) {
    assert.match(view.scriptSetup.content, /import projectLogo from '\.\.\/assets\/project-logo\.png'/)
    assert.match(view.template.content, /:src="projectLogo"/)
  }
  for (const template of [admin, worker, login]) {
    assert.match(template, /class="[^"]*brand-logo/)
    assert.doesNotMatch(template, /class="brand-mark"/)
  }
  assert.match(index, /rel="icon" href="\/src\/assets\/project-logo\.png"/)
  assert.match(index, /rel="apple-touch-icon" href="\/src\/assets\/project-logo\.png"/)
})

test('all application dropdowns use the shared below-field menu', () => {
  const names = [
    ['LeadershipReportView'], ['WorkshopView'], ['EnergyPeriodReportView'],
    ['CuttingDispatchView'], ['AutomationView'], ['DesignerView'], ['DatabaseView'],
    ['ReportForm', 'components'], ['ReportTable', 'components'], ['ReportExportMenu', 'components'],
    ['StandardReportQuery', 'components'],
  ]
  for (const [name, folder] of names) {
    const template = descriptor(name, folder).template.content
    assert.doesNotMatch(template, /<select\b/, `${name} still contains a native dropdown`)
  }

  const select = descriptor('AppSelect', 'components')
  assert.match(select.template.content, /<Teleport to="body">/)
  assert.match(select.scriptSetup.content, /width:\s*'max-content'/)
  assert.match(select.scriptSetup.content, /Math\.max\(rect\.width, menuNaturalSize\.value\.width\)/)
  assert.match(select.scriptSetup.content, /rect\.right - width/)
  assert.match(select.scriptSetup.content, /const openAbove = naturalHeight > availableBelow && availableAbove > availableBelow/)
  assert.match(select.scriptSetup.content, /openAbove \? rect\.top - gutter - renderedHeight : rect\.bottom \+ gutter/)
  assert.match(select.template.content, /role="listbox"/)
  assert.match(select.template.content, /aria-haspopup="listbox"/)
})

test('all report calendars use one shared below-field date picker', () => {
  const names = [
    ['LeadershipReportView'], ['WorkshopView'], ['EnergyPeriodReportView'], ['DatabaseView'],
    ['ReportForm', 'components'],
  ]
  for (const [name, folder] of names) {
    const template = descriptor(name, folder).template.content
    assert.doesNotMatch(template, /<input\b[^>]*type="(?:date|month)"/, `${name} still contains a native calendar`)
  }

  const picker = descriptor('AppDatePicker', 'components')
  assert.match(picker.template.content, /<Teleport to="body">/)
  assert.match(picker.scriptSetup.content, /top:\s*`\$\{Math\.round\(rect\.bottom \+ gutter\)\}px`/)
  assert.match(picker.template.content, /class="app-date-picker__months"/)
  assert.match(picker.template.content, /class="app-date-picker__days"/)
  assert.match(picker.template.content, />\{\{ mode === 'month' \? '本月' : '今天' \}\}</)
})

test('three standard reports reuse the quick period popover', () => {
  for (const name of ['LeadershipReportView', 'WorkshopView', 'EnergyPeriodReportView']) {
    const view = descriptor(name)
    assert.match(view.scriptSetup.content, /import StandardReportQuery from/)
    assert.match(view.template.content, /<StandardReportQuery/)
  }

  const query = descriptor('StandardReportQuery', 'components')
  assert.match(query.scriptSetup.content, /import ReportPeriodPicker from/)
  assert.match(query.template.content, /<ReportPeriodPicker/)
  const period = descriptor('ReportPeriodPicker', 'components')
  assert.match(period.template.content, /<Teleport to="body">/)
  assert.match(period.scriptSetup.content, /单日/)
  assert.match(period.scriptSetup.content, /最近 7 天/)
  assert.match(period.scriptSetup.content, /跨月·近 30 天/)
  assert.match(period.scriptSetup.content, /本季度/)
  assert.match(period.scriptSetup.content, /上季度/)
  assert.match(period.template.content, /@click="applyPeriod">应用/)
  assert.match(period.scriptSetup.content, /inclusiveDays > props\.maxDays/)
})

test('report panels share honest loading, empty and failed states without remounting charts', () => {
  const panel = descriptor('ReportTrendPanel', 'components')
  const panelState = descriptor('ReportPanelState', 'components')
  const query = descriptor('StandardReportQuery', 'components')

  assert.match(panel.scriptSetup.content, /import ReportPanelState from/)
  assert.match(panel.scriptSetup.content, /if \(props\.error && !props\.hasData\)/)
  assert.match(panel.scriptSetup.content, /if \(props\.loading && !props\.hasData\)/)
  assert.match(panel.scriptSetup.content, /if \(props\.empty && !props\.hasData\)/)
  assert.match(panel.template.content, /v-if="loading && hasData"[\s\S]*正在更新/)
  assert.match(panel.template.content, /'is-obscured': state/)
  assert.match(panel.template.content, /<ReportPanelState v-if="state"/)
  assert.doesNotMatch(panel.template.content, /v-if="!state"[^>]*ref="chartElement"/)
  assert.match(panelState.template.content, /type === 'error' \? 'alert' : 'status'/)
  assert.match(panelState.template.content, /aria-live="polite"/)
  assert.match(query.template.content, /:aria-busy="loading"/)
  assert.match(query.template.content, /loading \? '查询中' : hasData \? '更新报表' : '查询报表'/)
})

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
  assert.match(views[2].scriptSetup.content, /if \(value === null \|\| value === undefined \|\| !Number\.isFinite\(Number\(value\)\)\) return '--'/)
})

test('report export feedback can retry the exact last request', () => {
  const exportSource = readFileSync(new URL('../src/composables/useReportExport.js', import.meta.url), 'utf8')
  assert.match(exportSource, /lastRequest = \{ payload, format \}/)
  assert.match(exportSource, /function retryExport\(\)[\s\S]*exportReport\(lastRequest\.payload, lastRequest\.format\)/)

  for (const name of ['AdminLeadershipView', 'AdminWorkshopView', 'AdminEquipmentView']) {
    const view = descriptor(name)
    assert.match(view.scriptSetup.content, /retryExport/)
    assert.match(view.template.content, /<ReportFeedback/)
    assert.match(view.template.content, /action-label="重试导出"/)
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
  assert.match(script, /void loadAnalysis\(false, targetKey\)/)
  assert.match(script, /function reloadAnalysis\(\)[\s\S]*loadAnalysis\(true\)/)
  assert.match(script, /onDeactivated\(pauseAnalysis\)/)
  assert.match(script, /requestController\?\.abort\(\)/)
  assert.match(script, /analyzeReport\([\s\S]*\}, \{ signal \}\)/)
  assert.match(script, /dataVersion: analysisDataVersion\.value \|\| undefined/)
  assert.doesNotMatch(script, /报表数据分析|guardrail_fallback|simulated_fallback|降级/)
  assert.match(analysis.template.content, /class="analysis-mode model"[\s\S]*AI 分析/)
  assert.match(script, /const targetKey = requestKey\(\)[\s\S]*if \(!props\.ready \|\| !props\.reportDate\)/)
  assert.match(script, /if \(!props\.reportDate \|\| targetKey !== displayedRequestKey\) \{[\s\S]*analysis\.value = null/)

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

  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')
  assert.match(api, /analyzeReport\(\{[^}]*dataVersion[^}]*\}/)
  assert.match(api, /data_version: dataVersion \|\| undefined/)
  assert.match(script, /props\.summaryPeriodStart[\s\S]*props\.summaryPeriodEnd[\s\S]*props\.summaryDataVersion/)
})

test('report values use one decimal place throughout the UI', () => {
  const leadership = descriptor('LeadershipReportView').scriptSetup.content
  const workshop = descriptor('WorkshopView').scriptSetup.content
  const energy = descriptor('EnergyPeriodReportView').scriptSetup.content
  const home = descriptor('AdminHomeView').scriptSetup.content
  const database = descriptor('DatabaseView').scriptSetup.content
  const analysis = descriptor('AnalysisPanel', 'components').scriptSetup.content

  assert.match(leadership, /digits = 1/)
  assert.match(leadership, /minimumFractionDigits: 1/)
  assert.match(workshop, /minimumFractionDigits: 1, maximumFractionDigits: 1/)
  assert.match(energy, /digits = 1/)
  assert.match(home, /digits = 1/)
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

  assert.match(template, /class="[^"]*page-hero[^"]*report-hero--query[^"]*leadership-hero[^"]*"/)
  assert.equal((template.match(/class="leadership-kpi /g) || []).length, 4)
  assert.match(template, /<AnalysisPanel/)
  assert.doesNotMatch(template, /leadership-analysis/)
  assert.match(script, /report\.value\?\.comparison_total/)
  assert.match(script, /function changeRate\(current, previous\)/)
  assert.match(script, /defaultMonthlyView\.value \? '较上月' : '较对比区间'/)
  assert.match(styles, /--leadership-hero-image/)
  assert.match(styles, /grid-template-columns:repeat\(4,minmax\(0,1fr\)\)/)
})

test('equipment dashboard uses verified energy metrics and responsive home visuals', () => {
  const equipment = descriptor('EnergyPeriodReportView')
  const template = equipment.template.content
  const script = equipment.scriptSetup.content
  const styles = equipment.styles.map((style) => style.content).join('\n')

  assert.match(template, /class="[^"]*report-hero-query[^"]*device-query-shell[^"]*"/)
  assert.equal((template.match(/class="device-kpi /g) || []).length, 5)
  assert.match(template, /<AppIcon name="zap"/)
  assert.match(template, /<AppIcon name="wind"/)
  assert.match(template, /<AppIcon name="activity"/)
  assert.match(template, /<AppIcon name="tasks"/)
  assert.match(template, /<AppIcon name="clock"/)
  assert.match(script, /const energyCoverage = computed/)
  assert.doesNotMatch(template, /设备利用率/)
  assert.match(styles, /grid-template-columns:repeat\(5,minmax\(0,1fr\)\)/)
  assert.match(styles, /@media\(max-width:560px\)[\s\S]*\.device-query-shell \.report-query,\.device-kpis\{grid-template-columns:1fr/)
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

  assert.match(template, /class="[^"]*report-hero-query[^"]*workshop-query-shell[^"]*"/)
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
  assert.match(styles, /@media\(max-width:560px\)[\s\S]*\.workshop-query-shell \.report-query,\.workshop-kpis/)
})

test('home and equipment pages reveal independent results progressively', () => {
  const home = descriptor('AdminHomeView')
  const equipment = descriptor('EnergyPeriodReportView')

  assert.match(home.scriptSetup.content, /const overviewLoading = ref\(true\)/)
  assert.match(home.scriptSetup.content, /const historyLoading = ref\(true\)/)
  assert.doesNotMatch(home.template.content, /报表预加载/)
  assert.match(equipment.scriptSetup.content, /const taskRequest = getTaskEnergyDay/)
  assert.match(equipment.scriptSetup.content, /const summaryRequest = getEnergyMonthlyRange/)
  assert.match(equipment.scriptSetup.content, /await Promise\.allSettled\(\[taskRequest, summaryRequest\]\)/)
})

test('home quick access keeps three report links with readable business descriptions', () => {
  const home = descriptor('AdminHomeView')
  const template = home.template.content
  const styles = readFileSync(new URL('../src/assets/admin-home.css', import.meta.url), 'utf8')

  assert.match(template, /<nav class="quick-links" aria-label="核心报表快速入口">/)
  assert.equal((template.match(/class="quick-link quick-link--/g) || []).length, 3)
  assert.equal((template.match(/quick-link__copy"><strong>[^<]+<\/strong><small>[^<]+<\/small>/g) || []).length, 3)
  assert.match(styles, /\.quick-link \{[^}]*display: flex;/)
  assert.match(styles, /@media \(max-width: 760px\)/)
  assert.match(styles, /\.quick-link__arrow/)
})

test('home KPI and production ring show verified data instead of decorative placeholders', () => {
  const home = descriptor('AdminHomeView')
  const kpi = descriptor('HomeKpiCard', 'components')
  const template = home.template.content
  const script = home.scriptSetup.content

  assert.equal((template.match(/<HomeKpiCard /g) || []).length, 6)
  assert.match(kpi.template.content, /<MetricSparkline[^>]+:values="series"/)
  assert.match(script, /dailyRecords\.value\.map\(\(item\) => item\.cut_length_m\)/)
  assert.match(script, /energy\.value\?\.rows/)
  assert.match(script, /Number\(previous\) === 0\) return \{ text: '暂无上期对比'/)
  assert.match(template, /class="status-visual__value"[^>]*><strong>\{\{ activeDays \}\}<\/strong>/)
})

test('home KPI values stay fully readable when the card is narrow', () => {
  const styles = descriptor('HomeKpiCard', 'components').styles.map((style) => style.content).join('\n')

  assert.match(styles, /\.home-kpi\{[^}]*min-height:146px/)
  assert.match(styles, /\.home-kpi__reading\{[^}]*justify-content:center/)
  assert.match(styles, /\.home-kpi__reading\{[^}]*flex-wrap:wrap/)
  assert.match(styles, /\.home-kpi__reading strong\{[^}]*overflow-wrap:anywhere/)
  assert.doesNotMatch(styles, /\.home-kpi__reading strong\{[^}]*text-overflow:ellipsis/)
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
  assert.match(template, /number="02"[\s\S]*title="工人工时汇总"/)
  assert.doesNotMatch(template, /<section class="panel workshop-panel"|class="panel worker-time-summary"/)
  assert.doesNotMatch(template, /所选区间暂无工人确认数据/)
  assert.ok(template.indexOf('worker-time-summary') > template.indexOf('class="workshop-table"'))
  assert.match(css, /\.panel, \.job-card\s*\{[^}]*margin-bottom: 22px;/)
  assert.doesNotMatch(css, /\.worker-time-summary\s*\{[^}]*margin:/)
  assert.match(trendStyles, /\.report-trend-panel__content--fit-table \.report-trend-panel__table\s*\{[^}]*height:auto;[^}]*max-height:none;/)
})

test('source files are dispatched directly without a manual save step', () => {
  const dispatch = descriptor('CuttingDispatchView')
  assert.match(dispatch.template.content, /@click="openSinglePush\(row\)"/)
  assert.match(dispatch.template.content, /<strong>\{\{ row.document_no \}\}<\/strong>/)
  assert.match(dispatch.template.content, /设置参数并派发/)
  assert.match(dispatch.template.content, /标准名/)
  assert.match(dispatch.template.content, /非标准名/)
  assert.doesNotMatch(dispatch.template.content, /保存到系统|可推送文件列表/)
  assert.doesNotMatch(dispatch.scriptSetup.content, /CuttingDownloadsPanel|downloadsPanel/)
})

test('dispatch page keeps one directory browser and omits the saved-file picker', () => {
  const source = descriptor('CuttingDispatchView')
  assert.match(source.template.content, /<CatalogTreeNode/)
  assert.doesNotMatch(source.template.content, /<SortBuilder|<ConditionBuilder/)
  assert.doesNotMatch(source.scriptSetup.content, /CuttingDownloadsPanel|downloadsPanel/)
  assert.doesNotMatch(source.template.content, /<CuttingDownloadsPanel/)
  assert.doesNotMatch(source.template.content, /<h2>系统已保存文件<\/h2>/)
})

test('dispatch page loads once initially and refreshes runtime status on later activations', () => {
  const dispatch = descriptor('CuttingDispatchView')
  assert.match(dispatch.scriptSetup.content, /onMounted\(\(\) => \{\s*initializePage\(\)/)
  assert.match(dispatch.scriptSetup.content, /onActivated\(async \(\) =>/)
  assert.match(dispatch.scriptSetup.content, /if \(firstActivation\) \{[\s\S]*?return[\s\S]*?getCuttingStatus\('push', \{ signal \}\)/)
  assert.match(dispatch.scriptSetup.content, /onDeactivated\(pausePage\)/)
})

test('settings reuses the same compact navigation shell as reports', () => {
  const settings = descriptor('SettingsLayout', 'layouts')
  assert.match(settings.template.content, /class="report-navigation settings-navigation"/)
  assert.match(settings.template.content, /class="settings-home-link"/)
  assert.doesNotMatch(settings.template.content, /settings-header|settings-tabs/)
})

test('report navigation and report pages keep a visible separation', () => {
  const reports = descriptor('ReportsLayout', 'layouts')
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')

  assert.match(reports.template.content, /class="report-navigation reports-navigation"/)
  assert.match(styles, /\.reports-navigation\s*\{[^}]*margin-bottom:12px;/)
})

test('standard report pages share the light table header and compact panel tokens', () => {
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  assert.match(styles, /--report-table-head-bg:\s*#d8e7ee;/)
  assert.match(styles, /--report-table-head-ink:\s*#2f536b;/)
  assert.match(styles, /--report-panel-radius:\s*10px;/)

  for (const name of ['LeadershipReportView', 'WorkshopView', 'EnergyPeriodReportView']) {
    const viewStyles = descriptor(name).styles.map((style) => style.content).join('\n')
    assert.match(viewStyles, /background:var\(--report-table-head-bg\)!important;/, `${name} does not use the shared light table header`)
    assert.match(viewStyles, /border-radius:var\(--report-panel-radius\)/, `${name} does not use the shared report panel radius`)
  }
})

test('primary application pages share one readable typography scale', () => {
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  const tables = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')
  const settings = descriptor('SettingsHomeView')

  assert.match(styles, /--font-page-title:\s*38px;/)
  assert.match(styles, /--font-page-description:\s*12px;/)
  assert.match(styles, /--font-section-title:\s*14px;/)
  assert.match(styles, /--font-kpi-value:\s*22px;/)
  assert.match(styles, /--font-table-head:\s*10px;/)
  assert.match(styles, /--font-auxiliary:\s*9px;/)
  assert.match(tables, /font-size:11px !important;/)
  assert.match(tables, /font-size:10px !important;/)
  assert.doesNotMatch(styles, /font-family:\s*Georgia/)
  assert.doesNotMatch(settings.styles.map((style) => style.content).join('\n'), /Georgia/)
  assert.match(styles, /font-family: "Microsoft YaHei", "PingFang SC", Inter, Arial, sans-serif;/)
})

test('device report is one page with task details and a default monthly summary', () => {
  const container = descriptor('AdminEquipmentView')
  const energy = descriptor('EnergyPeriodReportView')

  assert.match(container.template.content, /<EnergyPeriodReportView[\s\S]*@export="exportReport"/)
  assert.doesNotMatch(container.template.content, /EquipmentReportView|reportTab|KeepAlive/)
  assert.match(energy.template.content, /<section class="[^"]*page-hero[^"]*report-hero--query[^"]*equipment-hero[^"]*">/)
  assert.match(energy.template.content, /<StandardReportQuery/)
  assert.doesNotMatch(energy.template.content, /报表类型|任务生产报表|change-report-tab/)
  assert.match(descriptor('StandardReportQuery', 'components').template.content, /<span><AppIcon name="chart"[^>]*\/>图表类型<\/span>/)
  assert.match(energy.template.content, /<h2>设备报表<\/h2>/)
  assert.match(energy.template.content, /:max-days="31"/)
  assert.match(energy.template.content, /任务能耗明细/)
  assert.match(energy.template.content, /<tfoot>[\s\S]*<th scope="row">汇总<\/th>/)
  assert.match(energy.scriptSetup.content, /近12个月月度能耗汇总/)
  assert.match(energy.template.content, /aria-label="任务能耗明细表"/)
  assert.match(energy.template.content, /:aria-label="summaryTableLabel"/)
  assert.match(energy.template.content, /class="leadership-table energy-month-table summary-table"/)
  assert.match(energy.template.content, /<thead><tr><th scope="col">序号<\/th><th scope="col">\{\{ summaryDateHeading \}\}<\/th><th scope="col">任务数<\/th><th scope="col">切割时长<\/th><th scope="col">电耗/)
  assert.match(energy.scriptSetup.content, /const taskChartRows = computed\(\(\) => taskRows\.value[\s\S]*\.slice\(0, 50\)[\s\S]*\.sort\(\(left, right\) => String\(left\.production_time \|\| ''\)\.localeCompare\(String\(right\.production_time \|\| ''\)\)\)\)/)
  assert.match(energy.scriptSetup.content, /const labels = taskChartRows\.value\.map/)
  assert.match(energy.scriptSetup.content, /getEnergyMonthlyRange\(recentStart, selectedDate/)
  assert.match(energy.template.content, /month\?\.total_task_count/)
  assert.doesNotMatch(energy.template.content, /电耗覆盖|气耗覆盖|保存状态/)
  assert.match(energy.template.content, /colspan="6" class="energy-empty"/)
  assert.doesNotMatch(energy.template.content, /任务结束/)
  assert.doesNotMatch(energy.template.content, /数据状态/)
  assert.match(energy.template.content, /colspan="8" class="energy-empty"/)
  assert.doesNotMatch(energy.template.content, /scope="row" colspan="5"/)
  assert.match(energy.template.content, /<tfoot><tr><th aria-hidden="true"><\/th><th scope="row">汇总<\/th>/)
  assert.doesNotMatch(energy.template.content, /当日任务汇总|当月汇总/)
  assert.equal((energy.template.content.match(/summary-table/g) || []).length, 2)
  assert.doesNotMatch(energy.template.content, /<colgroup>/)
  assert.match(energy.scriptSetup.content, /import AnalysisPanel from '\.\.\/components\/AnalysisPanel\.vue'/)
  assert.match(energy.template.content, /<AnalysisPanel[\s\S]*report-type="equipment"/)
  assert.match(energy.template.content, /:ready="analysisReady"/)
  assert.equal((energy.template.content.match(/<ReportTrendPanel/g) || []).length, 2)
  assert.equal((energy.template.content.match(/chart-label=/g) || []).length, 2)
  assert.match(energy.scriptSetup.content, /function renderCharts\(/)
  assert.match(energy.scriptSetup.content, /taskPanel\.value\?\.getChartElement\(\)/)
  const energyStyles = energy.styles.map((style) => style.content).join('\n')
  assert.doesNotMatch(energyStyles, /\.energy-period-query\s*\{[^}]*grid-template-columns:/)
  assert.doesNotMatch(energyStyles, /\.energy-period-query\s*\{[^}]*display:/)
})

test('report tables with sticky summaries use content-weighted window columns', () => {
  const leadership = descriptor('LeadershipReportView')
  const energy = descriptor('EnergyPeriodReportView')
  const trendPanel = descriptor('ReportTrendPanel', 'components')
  const contentCss = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')
  const trendStyles = trendPanel.styles.map((style) => style.content).join('\n')

  assert.equal((leadership.template.content.match(/leadership-table summary-table/g) || []).length, 2)
  assert.doesNotMatch(leadership.template.content, /<colgroup>/)
  assert.equal((energy.template.content.match(/summary-table/g) || []).length, 2)
  assert.doesNotMatch(energy.template.content, /<colgroup>/)
  assert.match(contentCss, /table-layout:fixed !important/)
  assert.match(trendStyles, /scrollbar-gutter:stable/)
  assert.match(trendStyles, /report-trend-panel__content--fit-table \.report-trend-panel__table \{[^}]*overflow-y:visible;[^}]*scrollbar-gutter:auto;/)
  for (const name of ['LeadershipReportView', 'EnergyPeriodReportView']) {
    const viewStyles = descriptor(name).styles.map((style) => style.content).join('\n')
    assert.match(viewStyles, /\.report-trend-panel__table\)\s*\{[^}]*overflow:auto!important;[^}]*scrollbar-gutter:stable;/, `${name} trend tables must remain scrollable`)
  }
  const workshopStyles = descriptor('WorkshopView').styles.map((style) => style.content).join('\n')
  assert.doesNotMatch(workshopStyles, /\.report-trend-panel__table\)\s*\{[^}]*overflow:auto!important;/)
  assert.match(contentCss, /height:47px !important/)
  assert.match(contentCss, /font-size:11px !important/)
  assert.match(contentCss, /thead th \{[\s\S]*font-size:10px !important/)
  assert.match(contentCss, /background:#0b4f76 !important/)
  assert.match(contentCss, /text-align:center !important/)
  assert.match(contentCss, /white-space:nowrap !important/)
})

test('device report reuses the leadership report UI baseline', () => {
  const leadership = descriptor('LeadershipReportView')
  const energy = descriptor('EnergyPeriodReportView')
  const trendPanel = descriptor('ReportTrendPanel', 'components')
  const energyScript = energy.scriptSetup.content
  const energyStyles = energy.styles.map((style) => style.content).join('\n')
  const trendStyles = trendPanel.styles.map((style) => style.content).join('\n')

  assert.match(leadership.template.content, /class="[^"]*page-hero[^"]*report-hero--query[^"]*leadership-hero[^"]*"/)
  assert.match(leadership.template.content, /<StandardReportQuery/)
  assert.match(energy.template.content, /class="[^"]*page-hero[^"]*report-hero--query[^"]*equipment-hero[^"]*"/)
  assert.match(energy.template.content, /<StandardReportQuery/)
  assert.equal((leadership.template.content.match(/<ReportTrendPanel/g) || []).length, 2)
  assert.equal((energy.template.content.match(/<ReportTrendPanel/g) || []).length, 2)
  assert.doesNotMatch(energy.template.content, /:height=/)
  assert.equal((energy.template.content.match(/chart-label=/g) || []).length, 2)
  assert.match(trendPanel.template.content, /class="panel report-trend-panel"/)
  assert.match(trendPanel.template.content, /class="panel-title report-trend-panel__heading"/)
  assert.match(trendPanel.template.content, /<slot name="summary"/)
  assert.match(trendPanel.template.content, /<slot name="table"/)
  assert.match(trendPanel.template.content, /<slot name="footer"/)
  assert.match(trendPanel.template.content, /v-if="showChart"/)
  assert.match(trendPanel.template.content, /report-trend-panel__content--table-only/)
  assert.match(trendPanel.scriptSetup.content, /defineExpose\(\{ getChartElement:/)
  assert.match(trendStyles, /\.report-trend-panel__content\s*\{[^}]*grid-template-columns:minmax\(330px,\.9fr\) minmax\(0,1\.25fr\)/)
  assert.match(trendStyles, /\.report-trend-panel__content--table-only\s*\{[^}]*grid-template-columns:minmax\(0,1fr\)/)
  assert.match(trendStyles, /height:var\(--report-trend-height\)/)
  assert.match(energyStyles, /\.energy-period-table thead th\s*\{[^}]*background:#0b4f76;/)
  assert.match(energyStyles, /\.energy-period-table th,\.energy-period-table td\s*\{[^}]*height:47px;/)
  assert.match(energyStyles, /\.energy-task-table th:nth-child\(2\),\.energy-task-table td:nth-child\(2\)/)
  assert.match(energyStyles, /\.energy-task-table tfoot th:nth-child\(2\),\.energy-task-table tfoot th:nth-child\(3\),\.energy-task-table tfoot th:nth-child\(4\)\s*\{[^}]*text-align:left;/)
  assert.doesNotMatch(energyStyles, /\.energy-period-table tfoot th:nth-child\(2\)/)
  assert.match(energyScript, /createReportChartGrid, REPORT_CHART_COLORS/)
  assert.match(energyScript, /grid: createReportChartGrid\(\)/)
  assert.match(energyScript, /REPORT_CHART_COLORS\.primary/)
  assert.match(energyScript, /REPORT_CHART_COLORS\.secondary/)
  assert.match(energyScript, /REPORT_CHART_COLORS\.accent/)
  assert.match(leadership.scriptSetup.content, /createReportChartGrid, REPORT_CHART_COLORS/)
  assert.match(leadership.scriptSetup.content, /grid: createReportChartGrid\(\)/)
  assert.match(energyScript, /function renderCharts\(/)
  assert.match(energyStyles, /@media\(max-width:560px\)[\s\S]*\.device-query-shell \.report-query,\.device-kpis\{grid-template-columns:1fr/)
})

test('equivalent page headers and report query bars share one layout system', () => {
  const leadership = descriptor('LeadershipReportView')
  const workshop = descriptor('WorkshopView')
  const energy = descriptor('EnergyPeriodReportView')
  const alarm = descriptor('AlarmCenterView')
  const database = descriptor('DatabaseView')
  const designer = descriptor('DesignerView')
  const automation = descriptor('AutomationView')
  const accounts = descriptor('AdminAccountsView')
  const dispatch = descriptor('CuttingDispatchView')
  const standardQuery = descriptor('StandardReportQuery', 'components')
  const standardQueryStyles = standardQuery.styles.map((style) => style.content).join('\n')
  const css = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')

  for (const view of [leadership, workshop, energy, alarm, dispatch]) {
    assert.match(view.template.content, /class="[^"]*page-hero[^"]*"/)
  }
  for (const view of [database, designer, automation, accounts]) {
    assert.match(view.scriptSetup.content, /import ManagementPageHero from/)
    assert.match(view.template.content, /<ManagementPageHero/)
  }
  for (const view of [leadership, workshop, energy]) {
    assert.match(view.scriptSetup.content, /import StandardReportQuery from/)
    assert.match(view.template.content, /<StandardReportQuery/)
    assert.doesNotMatch(view.scriptSetup.content, /import (?:ReportPeriodPicker|ReportExportMenu) from/)
    assert.doesNotMatch(view.template.content, /<(?:ReportPeriodPicker|ReportExportMenu)/)
    assert.match(view.template.content, /class="[^"]*report-hero-copy[^"]*"/)
    assert.match(view.template.content, /class="[^"]*report-hero-eyebrow[^"]*"/)
    assert.match(view.template.content, /class="report-hero-description"/)
    assert.match(view.template.content, /class="[^"]*report-hero-meta[^"]*"/)
  }
  for (const view of [leadership, workshop, energy]) {
    assert.match(view.template.content, /report-hero-query/)
  }
  assert.match(css, /\.report-hero\.report-hero--query\s*\{[^}]*grid-template-columns:minmax\(340px,\.72fr\) minmax\(0,1\.28fr\)!important;/)
  assert.match(css, /\.report-hero--query > \.report-hero-query\s*\{[^}]*grid-column:2;[^}]*grid-row:2;/)
  assert.match(css, /\.report-hero--query > \.report-hero-query\s*\{[^}]*border:1px solid #b9d0dc!important;[^}]*background:linear-gradient\(135deg,#f9fcfd 0%,#edf5f8 72%,#e2eef3 100%\)!important;[^}]*backdrop-filter:none;/)
  assert.match(css, /\.report-hero-query > \.standard-report-query\s*\{[^}]*width:100%!important;/)
  assert.match(css, /\.report-hero\.report-hero--query > \.report-hero-copy\s*\{[^}]*grid-template-rows:14px 48px minmax\(40px,auto\) 20px;/)
  assert.match(css, /\.report-hero-copy > h1,\.report-hero-copy > h2\s*\{[^}]*font-size:38px!important;/)
  assert.match(css, /--report-query-width:\s*884px;/)
  assert.match(css, /html\s*\{[^}]*scrollbar-gutter:stable;/)
  assert.match(css, /\.report-hero > div:first-child\s*\{[^}]*min-width:0;[^}]*flex:1 1 auto;/)
  assert.match(css, /\.report-hero > \.report-query\s*\{[^}]*flex:0 0 auto;/)
  assert.match(css, /\.report-query\s*\{[^}]*grid-template-columns:repeat\(2,190px\) 140px 136px 136px;/)
  assert.match(standardQuery.template.content, /class="standard-report-query"/)
  assert.match(standardQuery.template.content, /name="search"/)
  assert.match(standardQueryStyles, /\.standard-report-query\s*\{[^}]*grid-template-areas:"period chart query export";/)
  assert.match(standardQueryStyles, /\.standard-report-query :deep\(\.report-period-picker\)\s*\{[^}]*grid-template-rows:14px 40px;/)
  assert.match(standardQueryStyles, /\.standard-report-query :deep\(\.report-period-picker__trigger\),[\s\S]*?height:40px!important;[^}]*min-height:40px!important;/)
  assert.match(standardQueryStyles, /\.standard-report-query__submit\s*\{[^}]*margin-top:20px;/)
  assert.match(standardQueryStyles, /\.standard-report-query__export\s*\{[^}]*margin-top:20px;/)
  assert.match(css, /@media \(max-width: 900px\)[\s\S]*\.report-query\s*\{[^}]*grid-template-columns:repeat\(2,minmax\(180px,1fr\)\) 140px 136px 136px;/)
  assert.doesNotMatch(css, /\.workshop-query\s*\{[^}]*display:/)
  assert.doesNotMatch(css, /\.equipment-query\s+\.app-date-picker/)
  assert.doesNotMatch(energy.styles.map((style) => style.content).join('\n'), /\.equipment-hero|\.energy-period-query/)
  assert.match(css, /\.report-navigation nav a:focus-visible\s*\{[^}]*box-shadow:/)
})

test('report automation exposes secure live runtime configuration', () => {
  const automation = descriptor('AutomationView')
  const template = automation.template.content
  const script = automation.scriptSetup.content
  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')

  assert.match(template, /运行与邮件参数/)
  assert.match(template, /即时生效/)
  assert.doesNotMatch(template, /需重启生效/)
  assert.match(template, /configuration\.smtp_password_configured/)
  assert.match(template, /@submit\.prevent="submitConfiguration"/)
  assert.match(script, /smtp_password: configuration\.smtp_password \|\| null/)
  assert.match(script, /configurationMessage\.value = result\.message[\s\S]*?await loadAll\(\)/)
  assert.match(script, /Promise\.all\(\[loadConfiguration\(\), loadAll\(\)\]\)/)
  assert.match(api, /export function getAutomationConfiguration\(\)/)
  assert.match(api, /export function saveAutomationConfiguration\(payload\)/)
})

test('leadership report keeps refresh automatic and exposes only the normal query action', () => {
  const leadership = descriptor('LeadershipReportView')
  assert.doesNotMatch(leadership.template.content, /刷新最新数据|@click="refreshReport"/)
  assert.doesNotMatch(leadership.scriptSetup.content, /refreshLeadershipReport|function refreshReport/)
  assert.match(leadership.scriptSetup.content, /scheduleRefreshPoll/)
})

test('alarm center reuses the report UI baseline and padded empty states', () => {
  const alarm = descriptor('AlarmCenterView')
  const template = alarm.template.content
  const script = alarm.scriptSetup.content
  const styles = alarm.styles.map((style) => style.content).join('\n')

  assert.match(template, /class="page-hero report-hero alarm-hero"/)
  assert.match(template, /class="leadership-kpis alarm-kpis"/)
  assert.equal((template.match(/class="panel alarm-list"/g) || []).length, 2)
  assert.match(template, /class="alarm-refresh-button"/)
  assert.match(template, /<th>告警时间<\/th><th>发现时间<\/th>/)
  assert.match(template, /formatShortDateTime\(item\.first_start_time\)/)
  assert.match(template, /同步游标[\s\S]*ALARM_INFO_ID/)
  assert.doesNotMatch(template, /已同步记录/)
  assert.match(script, /item\?\.first_start_time \|\| item\?\.first_seen_at/)
  assert.match(script, /listAlarmIncidentPage/)
  assert.match(template, /class="alarm-pagination"/)
  assert.match(template, /第 \{\{ incidentPage \}\} \/ \{\{ incidentPageCount \}\} 页，共 \{\{ incidentSummary\.total \}\} 条/)
  assert.equal((template.match(/class="alarm-empty-state"/g) || []).length, 4)
  assert.match(template, /20kW 切割机当前无未解除告警/)
  assert.match(styles, /\.alarm-page>\.panel\{margin-bottom:0\}/)
  assert.match(styles, /\.alarm-content-grid>\.panel\{margin-bottom:0\}/)
  assert.match(styles, /\.alarm-empty-state\{[^}]*min-height:116px;[^}]*padding:26px;/)
  assert.match(styles, /\.alarm-table-wrap td\{font-size:13px\}/)
})

test('workshop confirmations support own revocation and audited admin correction', () => {
  const workshop = descriptor('WorkshopView')
  const script = workshop.scriptSetup.content
  const template = workshop.template.content
  const styles = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')

  assert.match(script, /row\.confirmed_by_id === props\.user\.user_id/)
  assert.match(script, /revokeWorkshopTask\(row\.task_id/)
  assert.match(script, /correctWorkshopTask\(row\.task_id/)
  assert.match(script, /listWorkshopConfirmationAudits/)
  assert.match(template, /撤销我的确认/)
  assert.match(template, /更正确认/)
  assert.match(template, /更正原因/)
  assert.match(script, /const correctionReasonPresets =/)
  assert.match(script, /workerRevoke:[\s\S]*adminRevoke:[\s\S]*adminReassign:/)
  assert.match(script, /function selectCorrectionReason\(reason\)/)
  assert.match(template, /aria-label="常用更正原因"/)
  assert.match(template, /v-for="reason in visibleCorrectionReasonPresets"/)
  assert.match(template, /选择常用原因，或直接填写其他原因/)
  assert.match(template, /该任务的确认记录/)
  assert.match(styles, /\.confirmation-person\s*\{[^}]*grid-template-columns:minmax\(0,1fr\) auto;[^}]*align-items:center;/)
  assert.match(styles, /\.correction-button\s*\{[^}]*white-space:nowrap!important;/)
  assert.match(styles, /\.confirmation-reason-presets button\.active\s*\{[^}]*background:#167e85;/)
})

test('intelligent analysis omits the analysis boundary section', () => {
  const analysis = descriptor('AnalysisPanel', 'components')
  assert.doesNotMatch(analysis.template.content, /分析边界|analysis\.limitations|analysis-limit/)
})

test('all standard reports share the leadership analysis structure and compact colored style', () => {
  for (const name of ['LeadershipReportView', 'WorkshopView', 'EnergyPeriodReportView']) {
    const template = descriptor(name).template.content
    assert.match(template, /<AnalysisPanel/)
    assert.doesNotMatch(template, /(?:leadership|workshop|device)-analysis/)
  }

  const analysis = descriptor('AnalysisPanel', 'components')
  const template = analysis.template.content
  const styles = analysis.styles.map((style) => style.content).join('\n')
  assert.match(template, /class="analysis-conclusion"/)
  assert.match(template, /分析结论/)
  assert.match(template, /核心指标/)
  assert.match(template, /分析结果/)
  assert.match(template, /class="analysis-facts"/)
  assert.match(template, /class="analysis-grid"/)
  assert.match(template, /class="analysis-evidence"/)
  assert.equal((template.match(/key: '(?:overview|features|alerts|suggestions)'/g) || []).length, 4)
  assert.match(styles, /\.analysis-facts\{[^}]*grid-template-columns:repeat\(6,minmax\(0,1fr\)\)/)
  assert.match(styles, /\.analysis-grid\s*\{[^}]*grid-template-columns:repeat\(2,minmax\(0,1fr\)\)!important;/)
  assert.match(styles, /\.analysis-panel\{[^}]*background:#fff;/)
  assert.match(styles, /\.analysis-grid article:nth-child\(3\)\{[^}]*background:#fff0f0!important;?/)
  assert.match(styles, /\.analysis-grid article:nth-child\(4\)\{[^}]*background:#eef8f4!important;?/)
  assert.match(styles, /\.analysis-panel-head\{[^}]*padding:13px 16px!important;/)
  assert.match(styles, /@media\(max-width:800px\)[\s\S]*\.analysis-grid\{[^}]*grid-template-columns:1fr!important;?/)
})

test('all standard report analyses follow the selected report range', () => {
  const leadership = descriptor('LeadershipReportView')
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const analysis = descriptor('AnalysisPanel', 'components')
  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')

  assert.match(leadership.template.content, /<AnalysisPanel[\s\S]*report-type="leadership"/)
  assert.match(leadership.template.content, /:period-start="defaultMonthlyView \? '' : appliedPeriodStart"/)
  assert.match(leadership.template.content, /:period-end="defaultMonthlyView \? '' : appliedPeriodEnd"/)
  assert.match(leadership.template.content, /:summary-period-start="monthlyPeriodStart"/)
  assert.match(leadership.template.content, /:summary-period-end="monthlyPeriodEnd"/)
  assert.doesNotMatch(leadership.template.content, /<AnalysisPanel v-else|区间智能分析将在导出报告时生成/)
  assert.match(workshop.template.content, /<AnalysisPanel[\s\S]*v-if="!isWorker"[\s\S]*report-type="workshop"/)
  assert.match(workshop.template.content, /:period-start="filters\.startDate"/)
  assert.match(workshop.template.content, /:period-end="filters\.endDate"/)
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

test('leadership and equipment second tables default to the recent twelve months', () => {
  const leadership = descriptor('LeadershipReportView')
  const equipment = descriptor('EnergyPeriodReportView')
  const leadershipRange = leadership.scriptSetup.content.match(/async function loadReport[\s\S]*?\n}\n\nasync function loadDefaultReport/)?.[0] || ''
  const equipmentRange = equipment.scriptSetup.content.match(/async function loadRange[\s\S]*?\n}\n\nfunction stopSummaryRefresh/)?.[0] || ''

  assert.match(leadership.scriptSetup.content, /getLeadershipReport\(today\.slice\(0, 7\), \{ force, signal \}\)/)
  assert.match(leadership.scriptSetup.content, /monthlyPeriodStart = ref\(recentTwelveMonthStart\(today\)\)/)
  assert.match(leadership.scriptSetup.content, /const monthlyReport = ref\(null\)/)
  assert.match(leadership.scriptSetup.content, /monthlyRecords = computed\(\(\) => monthlyReport\.value\?\.monthly_records/)
  assert.doesNotMatch(leadershipRange, /monthlyReport\.value|monthlyPeriodStart\.value|monthlyPeriodEnd\.value/)
  assert.match(leadershipRange, /renderCharts\(\{ monthly: false \}\)/)
  assert.match(leadership.template.content, /:title="monthlyTitle"/)
  assert.match(leadership.template.content, /:note="`\$\{monthlyPeriodStart\} 至 \$\{monthlyPeriodEnd\}`"/)
  assert.match(leadership.scriptSetup.content, /summary_period_start: monthlyPeriodStart\.value/)
  assert.match(leadership.scriptSetup.content, /summary_period_end: monthlyPeriodEnd\.value/)
  assert.match(equipment.scriptSetup.content, /getEnergyMonthlyRange\(recentStart, selectedDate/)
  assert.match(equipment.scriptSetup.content, /const monthlyReport = ref\(null\)/)
  assert.match(equipment.scriptSetup.content, /summaryRows = computed\(\(\) => sortReportPeriods\(monthlyReport\.value\?\.rows/)
  assert.match(equipmentRange, /beginRequest\(\{ preserveSummary: true \}\)/)
  assert.doesNotMatch(equipmentRange, /monthlyReport\.value|summaryPeriodStart\.value|summaryPeriodEnd\.value/)
  assert.match(equipmentRange, /renderCharts\(\{ summary: false \}\)/)
  assert.match(equipment.template.content, /:title="summaryTitle"/)
  assert.match(equipment.template.content, /:subtitle="summarySubtitle"/)
  assert.match(equipment.scriptSetup.content, /后台补齐 \$\{missing\} 天能耗中/)
  assert.match(equipment.scriptSetup.content, /scheduleSummaryRefresh\(summaryResult\)/)
  assert.match(equipment.scriptSetup.content, /}, 1000\)/)
  assert.match(equipment.scriptSetup.content, /summaryRefreshCount >= 600/)
  assert.match(equipment.scriptSetup.content, /summary_period_start: summaryPeriodStart\.value/)
  assert.match(equipment.scriptSetup.content, /summary_period_end: summaryPeriodEnd\.value/)
})

test('switching cached workshop and equipment reports does not force another query', () => {
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const preload = readFileSync(new URL('../src/state/reportPreload.js', import.meta.url), 'utf8')
  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')

  assert.match(workshop.scriptSetup.content, /force = false/)
  assert.match(workshop.scriptSetup.content, /if \(!pageActive\.value \|\| !isWorker\.value \|\| refreshTimer\) return/)
  assert.match(workshop.scriptSetup.content, /if \(isWorker\.value && result\.value\.report/)
  assert.doesNotMatch(equipment.scriptSetup.content, /onActivated\([\s\S]{0,300}getEnergyRange/)
  assert.match(preload, /listWorkshopRangeTasks\(\{ startDate: reportDate, endDate: reportDate \}\)/)
  assert.match(preload, /getEnergyMonthlyRange\(recentTwelveMonthStart\(reportDate\), reportDate\)/)
  assert.doesNotMatch(preload, /getEnergyRange\(recentTwelveMonthStart/)
  assert.match(api, /REPORT_VIEW_CACHE_TTL = 5 \* 60 \* 1000/)
})

test('workshop pagination does not invalidate an unchanged analysis context', () => {
  const view = descriptor('WorkshopView')
  const analysis = descriptor('AnalysisPanel', 'components').scriptSetup.content
  const workshop = view.scriptSetup.content
  const template = view.template.content
  const readyExpression = workshop.match(/const analysisReady = computed\(\(\) => Boolean\(([\s\S]*?)\n\)\)/)

  assert.ok(readyExpression)
  assert.match(readyExpression[1], /!loading\.value/)
  assert.match(analysis, /if \(!props\.reportDate \|\| targetKey !== displayedRequestKey\) \{[\s\S]*analysis\.value = null/)
  assert.match(template, /@click="load\(result\.page - 1\)">上一页/)
  assert.match(template, /@click="load\(result\.page \+ 1\)">下一页/)
})

test('cached report resources pause on deactivation and cache is bounded', () => {
  const layout = descriptor('ReportsLayout', 'layouts')
  const leadership = descriptor('LeadershipReportView')
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const controls = ['AppSelect', 'AppDatePicker', 'ReportPeriodPicker']
    .map((name) => descriptor(name, 'components').scriptSetup.content)

  assert.match(layout.template.content, /<KeepAlive :max="3">/)
  for (const view of [leadership, workshop, equipment]) {
    assert.match(view.scriptSetup.content, /onDeactivated\(/)
    assert.match(view.template.content, /v-content-table="pageActive"/)
  }
  assert.match(leadership.scriptSetup.content, /disposeCharts\(\)/)
  assert.match(equipment.scriptSetup.content, /disposeCharts\(\)/)
  assert.match(leadership.scriptSetup.content, /onBeforeUnmount\(\(\) => pausePage\(true\)\)/)
  assert.match(equipment.scriptSetup.content, /onBeforeUnmount\(\(\) => pause\(true\)\)/)
  assert.match(leadership.scriptSetup.content, /dailyChart \|\|= init\(dailyChartElement\)/)
  assert.match(equipment.scriptSetup.content, /taskChart \|\|= init\(taskChartElement\)/)
  assert.doesNotMatch(leadership.scriptSetup.content, /onDeactivated\([\s\S]{0,120}disposeCharts/)
  assert.doesNotMatch(equipment.scriptSetup.content, /onDeactivated\([\s\S]{0,120}disposeCharts/)
  for (const control of controls) {
    assert.match(control, /createActivationBinding/)
    assert.match(control, /onActivated\(listenerBinding\.activate\)/)
    assert.match(control, /onDeactivated\(pause\)/)
  }
})

test('long-lived page polling is serialized and cancelled when the page leaves', () => {
  const alarm = descriptor('AlarmCenterView').scriptSetup.content
  const workshop = descriptor('WorkshopView').scriptSetup.content
  const reportCenter = descriptor('ReportCenterView').scriptSetup.content
  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')

  assert.doesNotMatch(alarm, /setInterval\(loadData/)
  assert.match(alarm, /if \(!pageActive \|\| requestController\) return/)
  assert.match(alarm, /requestController\?\.abort\(\)/)
  assert.match(alarm, /await loadData\(\)[\s\S]*scheduleRefresh\(\)/)
  assert.match(api, /getAlarmStatus\(\{ signal \} = \{\}\)/)
  assert.match(api, /listAlarmIncidentPage\([\s\S]*\{ signal \} = \{\}\)/)
  assert.match(api, /listAlarmReports\([\s\S]*\{ signal \} = \{\}\)/)

  assert.doesNotMatch(workshop, /setInterval\(/)
  assert.match(workshop, /refreshTimer = window\.setTimeout\(async \(\) =>/)
  assert.match(workshop, /await load\(result\.value\.page,[\s\S]*startRefreshTimer\(\)/)

  assert.match(reportCenter, /function stopPolling\(\)[\s\S]*pollController\?\.abort\(\)/)
  assert.match(reportCenter, /getReportStatus\(reportId, \{ signal: controller\.signal \}\)/)
  assert.match(reportCenter, /if \(!pageActive \|\| controller\.signal\.aborted \|\| sequence !== pollSequence\) return/)
  assert.match(reportCenter, /listReports\(\{ page, pageSize: historyPageSize\.value, force, signal: controller\.signal \}\)/)
  assert.match(reportCenter, /sequence !== historySequence/)
  assert.match(reportCenter, /onBeforeUnmount\(\(\) => \{[\s\S]*pageActive = false[\s\S]*stopPolling\(\)/)
})

test('energy report omits calculation-method and complete-day explanations', () => {
  const energy = descriptor('EnergyPeriodReportView')
  const template = energy.template.content

  assert.doesNotMatch(template, /完整|部分合计|energy_notice|storage_notice|energy-method-note/)
  assert.doesNotMatch(template, /累计读数差值|采样规则|线性插值/)
})

test('scrollable report tables do not add duplicate previous and next pagination', () => {
  const energy = descriptor('EnergyPeriodReportView')
  const script = energy.scriptSetup.content
  const template = energy.template.content

  assert.match(template, /v-for="\(row, index\) in taskRows"/)
  assert.doesNotMatch(template, /#footer|上一页|下一页/)
  assert.doesNotMatch(script, /visibleTaskRows|totalPages|pageSize/)
})

test('file dispatch uses PDM references and has no saved-file prerequisite', () => {
  const dispatch = descriptor('CuttingDispatchView')

  assert.match(dispatch.template.content, /<aside class="panel directory-panel">/)
  assert.match(dispatch.template.content, /目录文件/)
  assert.match(dispatch.scriptSetup.content, /document_id: item\.documentId/)
  assert.match(dispatch.scriptSetup.content, /version_id: item\.versionId/)
  assert.match(dispatch.scriptSetup.content, /file_path: item\.filePath/)
  assert.doesNotMatch(dispatch.scriptSetup.content, /listCuttingDownloads|downloadCuttingFile/)
  assert.match(dispatch.template.content, /批量派发确认/)
  assert.match(dispatch.template.content, /确认并顺序派发/)
  assert.match(dispatch.template.content, /PDM项目目录/)
})
