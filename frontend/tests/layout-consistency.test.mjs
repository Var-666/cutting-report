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

test('device report is one page with daily task and monthly daily tables', () => {
  const container = descriptor('AdminEquipmentView')
  const energy = descriptor('EnergyPeriodReportView')

  assert.match(container.template.content, /<EnergyPeriodReportView[\s\S]*@export="exportReport"/)
  assert.doesNotMatch(container.template.content, /EquipmentReportView|reportTab|KeepAlive/)
  assert.match(energy.template.content, /<section class="page-hero report-hero equipment-hero">/)
  assert.match(energy.template.content, /class="report-query equipment-query energy-period-query"/)
  assert.doesNotMatch(energy.template.content, /报表类型|任务生产报表|change-report-tab/)
  assert.match(energy.template.content, /<h2>设备报表<\/h2>/)
  assert.match(energy.template.content, /任务能耗明细/)
  assert.match(energy.template.content, /<tfoot>[\s\S]*<th scope="row">汇总<\/th>/)
  assert.match(energy.template.content, /月度每日能耗汇总/)
  assert.match(energy.template.content, /aria-label="任务能耗明细表"/)
  assert.match(energy.template.content, /aria-label="月度每日能耗汇总表"/)
  assert.match(energy.template.content, /class="leadership-table energy-month-table summary-table"/)
  assert.match(energy.template.content, /<thead><tr><th scope="col">日期<\/th><th scope="col">任务数<\/th><th scope="col">切割时长<\/th><th scope="col">电耗/)
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
  assert.equal((energy.template.content.match(/class="energy-period-split"/g) || []).length, 2)
  const energyStyles = energy.styles.map((style) => style.content).join('\n')
  assert.doesNotMatch(energyStyles, /\.energy-period-query\s*\{[^}]*grid-template-columns:/)
  assert.doesNotMatch(energyStyles, /\.energy-period-query\s*\{[^}]*display:/)
})

test('report tables with sticky summaries use content-weighted window columns', () => {
  const leadership = descriptor('LeadershipReportView')
  const energy = descriptor('EnergyPeriodReportView')
  const contentCss = readFileSync(new URL('../src/assets/content-tables.css', import.meta.url), 'utf8')

  assert.equal((leadership.template.content.match(/leadership-table summary-table/g) || []).length, 2)
  assert.doesNotMatch(leadership.template.content, /<colgroup>/)
  assert.equal((energy.template.content.match(/summary-table/g) || []).length, 2)
  assert.doesNotMatch(energy.template.content, /<colgroup>/)
  assert.match(contentCss, /table-layout:fixed !important/)
  assert.match(contentCss, /scrollbar-gutter:stable/)
  assert.match(contentCss, /height:47px !important/)
  assert.match(contentCss, /font-size:13px !important/)
  assert.match(contentCss, /background:#0b4f76 !important/)
  assert.doesNotMatch(contentCss, /text-align:center !important/)
})

test('device report reuses the leadership report UI baseline', () => {
  const leadership = descriptor('LeadershipReportView')
  const energy = descriptor('EnergyPeriodReportView')
  const energyScript = energy.scriptSetup.content
  const energyStyles = energy.styles.map((style) => style.content).join('\n')

  assert.match(leadership.template.content, /class="page-hero report-hero leadership-hero"/)
  assert.match(leadership.template.content, /class="report-query month-query"/)
  assert.match(energy.template.content, /class="page-hero report-hero equipment-hero"/)
  assert.match(energy.template.content, /class="report-query equipment-query energy-period-query"/)
  assert.equal((energy.template.content.match(/class="panel-title energy-section-heading"/g) || []).length, 2)
  assert.match(energyStyles, /\.energy-period-chart\s*\{[^}]*height:260px;/)
  assert.match(energyStyles, /\.energy-period-table-wrap\s*\{[^}]*height:260px;[^}]*max-height:260px;/)
  assert.match(energyStyles, /\.energy-period-table thead th\s*\{[^}]*background:#0b4f76;/)
  assert.match(energyStyles, /\.energy-period-table th,\.energy-period-table td\s*\{[^}]*height:47px;/)
  assert.match(energyStyles, /\.energy-task-table th:nth-child\(2\),\.energy-task-table td:nth-child\(2\)/)
  assert.match(energyStyles, /\.energy-task-table tfoot th:nth-child\(2\),\.energy-task-table tfoot th:nth-child\(3\),\.energy-task-table tfoot th:nth-child\(4\)\s*\{[^}]*text-align:left;/)
  assert.doesNotMatch(energyStyles, /\.energy-period-table tfoot th:nth-child\(2\)/)
  assert.match(energyScript, /grid:\s*\{ left: 22, right: 28, bottom: 18, top: 54, containLabel: true \}/)
  assert.doesNotMatch(energyScript, /grid:\s*\{ left: 58, right: 58, bottom: 52/)
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
  assert.match(css, /--report-query-width:\s*684px;/)
  assert.match(css, /\.report-query\s*\{[^}]*grid-template-columns:190px 150px 140px 136px;/)
  assert.doesNotMatch(css, /\.workshop-query\s*\{[^}]*display:/)
  assert.doesNotMatch(css, /\.equipment-query\s+\.app-date-picker/)
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

  assert.match(script, /row\.confirmed_by_id === props\.user\.user_id/)
  assert.match(script, /revokeWorkshopTask\(row\.task_id/)
  assert.match(script, /correctWorkshopTask\(row\.task_id/)
  assert.match(script, /listWorkshopConfirmationAudits/)
  assert.match(template, /撤销我的确认/)
  assert.match(template, /更正确认/)
  assert.match(template, /更正原因/)
  assert.match(template, /该任务的确认记录/)
})

test('intelligent analysis omits the analysis boundary section', () => {
  const analysis = descriptor('AnalysisPanel', 'components')
  assert.doesNotMatch(analysis.template.content, /分析边界|analysis\.limitations|analysis-limit/)
})

test('energy report omits calculation-method and complete-day explanations', () => {
  const energy = descriptor('EnergyPeriodReportView')
  const template = energy.template.content

  assert.doesNotMatch(template, /完整|部分合计|energy_notice|storage_notice|energy-method-note/)
  assert.doesNotMatch(template, /累计读数差值|采样规则|线性插值/)
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
