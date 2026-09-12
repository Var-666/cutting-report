import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

test('all application dropdowns use the shared below-field menu', () => {
  const names = [
    ['LeadershipReportView'], ['WorkshopView'], ['EnergyPeriodReportView'],
    ['CuttingDispatchView'], ['AutomationView'], ['DesignerView'], ['DatabaseView'],
    ['ReportForm', 'components'], ['ReportTable', 'components'], ['ReportExportMenu', 'components'],
  ]
  for (const [name, folder] of names) {
    const template = descriptor(name, folder).template.content
    assert.doesNotMatch(template, /<select\b/, `${name} still contains a native dropdown`)
  }

  const select = descriptor('AppSelect', 'components')
  assert.match(select.template.content, /<Teleport to="body">/)
  assert.match(select.scriptSetup.content, /top:\s*`\$\{Math\.round\(rect\.bottom \+ gutter\)\}px`/)
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
    assert.match(view.scriptSetup.content, /import ReportPeriodPicker from/)
    assert.match(view.template.content, /<ReportPeriodPicker/)
  }

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

test('source files are saved to the system and are not downloadable to the browser', () => {
  const downloads = descriptor('CuttingDownloadsPanel', 'components')
  const dispatch = descriptor('CuttingDispatchView')
  assert.doesNotMatch(downloads.template.content, /download-source-file|下载所选文件|<span>03<\/span>/)
  assert.match(dispatch.template.content, /@click="downloadFile\(row\)"/)
  assert.match(dispatch.template.content, /<strong>\{\{ row.document_no \}\}<\/strong>/)
  assert.match(downloads.template.content, /<h2>系统已保存文件<\/h2>/)
  assert.doesNotMatch(downloads.template.content, /另存到电脑|选择电脑保存位置|downloaded-file/)
  assert.match(dispatch.template.content, /保存到系统/)
  assert.match(dispatch.template.content, /aria-label="可推送文件列表"/)
  assert.match(dispatch.template.content, /v-model="pushFileDraft.keyword"/)
  assert.match(dispatch.template.content, /v-model="pushFileDraft.projectId"/)
  assert.match(dispatch.template.content, /v-model="pushFileDraft.catalogKey"/)
  assert.doesNotMatch(dispatch.template.content, /v-model="pushFileDraft\.(version|owner)"/)
  assert.match(dispatch.template.content, /@change="selectPushFile\(item\)"/)
  assert.match(downloads.template.content, /<th>版本 \/ 来源<\/th>/)
})

test('source and saved files use the same directory-tree interaction', () => {
  const source = descriptor('CuttingDispatchView')
  assert.match(source.template.content, /<CatalogTreeNode/)
  assert.doesNotMatch(source.template.content, /<SortBuilder|<ConditionBuilder/)
  const downloaded = descriptor('CuttingDownloadsPanel', 'components')
  assert.match(downloaded.template.content, /<CatalogTreeNode/)
  assert.match(downloaded.scriptSetup.content, /field: 'project_id'/)
  assert.match(downloaded.scriptSetup.content, /field: 'catalog_id'/)
  assert.match(downloaded.template.content, /全部已保存文件/)
  assert.match(downloaded.scriptSetup.content, /const projectOptions = ref\(\[\]\)/)
  assert.match(downloaded.scriptSetup.content, /const selectedProjectId = ref\(props\.projectId\)/)
  assert.match(downloaded.scriptSetup.content, /listCuttingProjects\(\)/)
  assert.match(downloaded.template.content, /id="saved-project-selector"/)
  assert.match(downloaded.template.content, /v-for="project in projectOptions"/)
  assert.doesNotMatch(downloaded.scriptSetup.content, /君正25900-9SS|defaultProjectKeyword/)
  assert.match(downloaded.scriptSetup.content, /selectedCatalog\.value\s*\?\s*\[\{ field: 'catalog_id'/)
  assert.doesNotMatch(downloaded.template.content, /<SortBuilder|<ConditionBuilder|可增减条件/)
})

test('push page refreshes saved files whenever its cached view becomes active', () => {
  const dispatch = descriptor('CuttingDispatchView')
  assert.match(dispatch.scriptSetup.content, /onActivated\(async \(\) =>/)
  assert.match(dispatch.scriptSetup.content, /await loadPushFiles\(\)/)
})

test('settings reuses the same compact navigation shell as reports', () => {
  const settings = descriptor('SettingsLayout', 'layouts')
  assert.match(settings.template.content, /class="report-navigation settings-navigation"/)
  assert.match(settings.template.content, /class="settings-home-link"/)
  assert.doesNotMatch(settings.template.content, /settings-header|settings-tabs/)
})

test('device report is one page with task details and a default monthly summary', () => {
  const container = descriptor('AdminEquipmentView')
  const energy = descriptor('EnergyPeriodReportView')

  assert.match(container.template.content, /<EnergyPeriodReportView[\s\S]*@export="exportReport"/)
  assert.doesNotMatch(container.template.content, /EquipmentReportView|reportTab|KeepAlive/)
  assert.match(energy.template.content, /<section class="page-hero report-hero equipment-hero">/)
  assert.match(energy.template.content, /class="report-query equipment-query energy-period-query"/)
  assert.doesNotMatch(energy.template.content, /报表类型|任务生产报表|change-report-tab/)
  assert.match(energy.template.content, /<span>图表类型<\/span>/)
  assert.match(energy.template.content, /<h2>设备报表<\/h2>/)
  assert.match(energy.template.content, /:max-days="31"/)
  assert.match(energy.template.content, /任务能耗明细/)
  assert.match(energy.template.content, /<tfoot>[\s\S]*<th scope="row">汇总<\/th>/)
  assert.match(energy.scriptSetup.content, /近12个月月度能耗汇总/)
  assert.match(energy.template.content, /aria-label="任务能耗明细表"/)
  assert.match(energy.template.content, /:aria-label="summaryTableLabel"/)
  assert.match(energy.template.content, /class="leadership-table energy-month-table summary-table"/)
  assert.match(energy.template.content, /<thead><tr><th scope="col">\{\{ summaryDateHeading \}\}<\/th><th scope="col">任务数<\/th><th scope="col">切割时长<\/th><th scope="col">电耗/)
  assert.match(energy.scriptSetup.content, /getEnergyMonthlyRange\(recentStart, selectedDate/)
  assert.match(energy.template.content, /month\?\.total_task_count/)
  assert.doesNotMatch(energy.template.content, /电耗覆盖|气耗覆盖|保存状态/)
  assert.match(energy.template.content, /colspan="5" class="energy-empty"/)
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
  assert.match(energy.scriptSetup.content, /function renderCharts\(\)/)
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
  assert.match(contentCss, /height:47px !important/)
  assert.match(contentCss, /font-size:13px !important/)
  assert.match(contentCss, /background:#0b4f76 !important/)
  assert.doesNotMatch(contentCss, /text-align:center !important/)
})

test('device report reuses the leadership report UI baseline', () => {
  const leadership = descriptor('LeadershipReportView')
  const energy = descriptor('EnergyPeriodReportView')
  const trendPanel = descriptor('ReportTrendPanel', 'components')
  const energyScript = energy.scriptSetup.content
  const energyStyles = energy.styles.map((style) => style.content).join('\n')
  const trendStyles = trendPanel.styles.map((style) => style.content).join('\n')

  assert.match(leadership.template.content, /class="page-hero report-hero leadership-hero"/)
  assert.match(leadership.template.content, /class="report-query month-query"/)
  assert.match(energy.template.content, /class="page-hero report-hero equipment-hero"/)
  assert.match(energy.template.content, /class="report-query equipment-query energy-period-query"/)
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
  assert.match(energyScript, /function renderCharts\(\)/)
  assert.match(energyStyles, /@media \(max-width:560px\)\s*\{[^}]*\.energy-stat-strip\s*\{\s*grid-template-columns:1fr;/)
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
  const css = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')

  for (const view of [leadership, workshop, energy, alarm, database, designer, automation, accounts, dispatch]) {
    assert.match(view.template.content, /class="[^"]*page-hero[^"]*"/)
  }
  for (const view of [leadership, workshop, energy]) {
    assert.match(view.template.content, /class="[^"]*report-query[^"]*"/)
  }
  assert.match(css, /--report-query-width:\s*884px;/)
  assert.match(css, /html\s*\{[^}]*scrollbar-gutter:stable;/)
  assert.match(css, /\.report-hero > div:first-child\s*\{[^}]*min-width:0;[^}]*flex:1 1 auto;/)
  assert.match(css, /\.report-hero > \.report-query\s*\{[^}]*flex:0 0 auto;/)
  assert.match(css, /\.report-query\s*\{[^}]*grid-template-columns:repeat\(2,190px\) 140px 136px 136px;/)
  assert.match(css, /@media \(max-width: 900px\)[\s\S]*\.report-query\s*\{[^}]*grid-template-columns:repeat\(2,minmax\(180px,1fr\)\) 140px 136px 136px;/)
  assert.doesNotMatch(css, /\.workshop-query\s*\{[^}]*display:/)
  assert.doesNotMatch(css, /\.equipment-query\s+\.app-date-picker/)
  assert.doesNotMatch(energy.styles.map((style) => style.content).join('\n'), /\.equipment-hero|\.energy-period-query/)
  assert.match(css, /\.report-navigation nav a:focus-visible\s*\{[^}]*box-shadow:/)
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
  const styles = alarm.styles.map((style) => style.content).join('\n')

  assert.match(template, /class="page-hero report-hero alarm-hero"/)
  assert.match(template, /class="leadership-kpis alarm-kpis"/)
  assert.equal((template.match(/class="panel alarm-list"/g) || []).length, 2)
  assert.match(template, /class="alarm-refresh-button"/)
  assert.equal((template.match(/class="alarm-empty-state"/g) || []).length, 4)
  assert.match(template, /20kW 切割机当前无未解除告警/)
  assert.match(styles, /\.alarm-page>\.panel\{margin-bottom:0\}/)
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
  assert.match(template, /该任务的确认记录/)
  assert.match(styles, /\.confirmation-person\s*\{[^}]*grid-template-columns:minmax\(0,1fr\) auto;[^}]*align-items:center;/)
  assert.match(styles, /\.correction-button\s*\{[^}]*white-space:nowrap!important;/)
})

test('intelligent analysis omits the analysis boundary section', () => {
  const analysis = descriptor('AnalysisPanel', 'components')
  assert.doesNotMatch(analysis.template.content, /分析边界|analysis\.limitations|analysis-limit/)
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
  assert.doesNotMatch(leadership.template.content, /<AnalysisPanel v-else|区间智能分析将在导出报告时生成/)
  assert.match(workshop.template.content, /<AnalysisPanel[\s\S]*v-if="!isWorker"[\s\S]*report-type="workshop"/)
  assert.match(workshop.template.content, /:period-start="filters\.startDate"/)
  assert.match(workshop.template.content, /:period-end="filters\.endDate"/)
  assert.doesNotMatch(workshop.template.content, /filters\.startDate === filters\.endDate/)
  assert.match(equipment.template.content, /<AnalysisPanel[\s\S]*report-type="equipment"/)
  assert.match(equipment.template.content, /:period-start="defaultSummaryView \? '' : appliedPeriodStart"/)
  assert.match(equipment.template.content, /:period-end="defaultSummaryView \? '' : appliedPeriodEnd"/)
  assert.doesNotMatch(equipment.template.content, /<AnalysisPanel v-else|区间智能分析将在导出报告时生成/)
  assert.match(analysis.scriptSetup.content, /periodStart: props\.periodStart \|\| undefined/)
  assert.match(analysis.scriptSetup.content, /periodEnd: props\.periodEnd \|\| undefined/)
  assert.match(api, /period_start: periodStart/)
  assert.match(api, /period_end: periodEnd/)
})

test('leadership and equipment second tables default to the recent twelve months', () => {
  const leadership = descriptor('LeadershipReportView')
  const equipment = descriptor('EnergyPeriodReportView')

  assert.match(leadership.scriptSetup.content, /getLeadershipReport\(today\.slice\(0, 7\)\)/)
  assert.match(leadership.scriptSetup.content, /monthlyPeriodStart = ref\(recentTwelveMonthStart\(today\)\)/)
  assert.match(leadership.template.content, /:title="monthlyTitle"/)
  assert.match(leadership.template.content, /:note="`\$\{monthlyPeriodStart\} 至 \$\{monthlyPeriodEnd\}`"/)
  assert.match(equipment.scriptSetup.content, /getEnergyMonthlyRange\(recentStart, selectedDate/)
  assert.match(equipment.scriptSetup.content, /summaryPeriodStart\.value = requestedStart/)
  assert.match(equipment.template.content, /:title="summaryTitle"/)
  assert.match(equipment.template.content, /:subtitle="summarySubtitle"/)
  assert.match(equipment.scriptSetup.content, /后台补齐 \$\{missing\} 天能耗中/)
  assert.match(equipment.scriptSetup.content, /scheduleSummaryRefresh\(summaryResult\)/)
  assert.match(equipment.scriptSetup.content, /}, 1000\)/)
  assert.match(equipment.scriptSetup.content, /summaryRefreshCount >= 600/)
})

test('switching cached workshop and equipment reports does not force another query', () => {
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const preload = readFileSync(new URL('../src/state/reportPreload.js', import.meta.url), 'utf8')
  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')

  assert.match(workshop.scriptSetup.content, /force = false/)
  assert.match(workshop.scriptSetup.content, /if \(!isWorker\.value \|\| refreshTimer\) return/)
  assert.match(workshop.scriptSetup.content, /if \(isWorker\.value && result\.value\.report/)
  assert.doesNotMatch(equipment.scriptSetup.content, /onActivated\([\s\S]{0,300}getEnergyRange/)
  assert.match(preload, /listWorkshopRangeTasks\(\{ startDate: reportDate, endDate: reportDate \}\)/)
  assert.match(preload, /getEnergyMonthlyRange\(recentTwelveMonthStart\(reportDate\), reportDate\)/)
  assert.doesNotMatch(preload, /getEnergyRange\(recentTwelveMonthStart/)
  assert.match(api, /REPORT_VIEW_CACHE_TTL = 5 \* 60 \* 1000/)
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

test('file push uses downloaded-file parameters without a condition search', () => {
  const dispatch = descriptor('CuttingDispatchView')

  assert.match(dispatch.template.content, /<aside v-if="isDownload" class="panel directory-panel">/)
  assert.match(dispatch.template.content, /选择系统已保存文件/)
  assert.match(dispatch.scriptSetup.content, /const pushFilePageSize = 20/)
  assert.match(dispatch.scriptSetup.content, /filteredPushFiles/)
  assert.match(dispatch.scriptSetup.content, /downloadCatalogLabel/)
  assert.match(dispatch.scriptSetup.content, /listCuttingDownloads\(\{ page: 1, pageSize: 100 \}\)/)
  assert.match(dispatch.scriptSetup.content, /download_id: activeFile\.value\.download_id/)
  assert.match(dispatch.template.content, /taskName 与实际上传文件名一致/)
  assert.match(dispatch.template.content, /PDM项目目录/)
})
