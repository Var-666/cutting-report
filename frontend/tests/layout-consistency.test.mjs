import { renderComponent } from './vue-component-helpers.mjs'
import { scriptFunction } from './typescript-helpers.mjs'
import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { compileScript, parse } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { useEnergyReportData } from '../src/composables/useEnergyReportData.ts'
import { computed, reactive } from 'vue'
import { transformSync } from 'esbuild'
import { getAutomationConfiguration, saveAutomationConfiguration } from '../src/api/automation.ts'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

const energyDataScript = readFileSync(new URL('../src/composables/useEnergyReportData.ts', import.meta.url), 'utf8')
const energyChartsScript = readFileSync(new URL('../src/composables/useEnergyReportCharts.ts', import.meta.url), 'utf8')

const leadershipQueryScript = readFileSync(new URL('../src/composables/useLeadershipReportQuery.ts', import.meta.url), 'utf8')
const leadershipDataScript = readFileSync(new URL('../src/composables/useLeadershipReportData.ts', import.meta.url), 'utf8')
const leadershipChartsScript = readFileSync(new URL('../src/composables/useLeadershipReportCharts.ts', import.meta.url), 'utf8')

test('vertical-mix reserves both device columns on every page and uses a single mobile drawer', () => {
  const layout = descriptor('AdminLayout', 'layouts')
  const script = layout.scriptSetup.content.slice(layout.scriptSetup.content.indexOf('function getSiderAndCollapsedWidth'))
  const themeStore = { sider: { width: 220, collapsedWidth: 64, mixWidth: 64, mixCollapsedWidth: 48, mixChildMenuWidth: 180 } }
  const appStore = reactive({ isMobile: false, mixSiderFixed: true })
  const isVerticalMix = computed(() => true)
  const inactive = computed(() => false)
  const code = transformSync(script, { loader: 'ts' }).code
  const width = scriptFunction('themeStore', 'appStore', 'isVerticalMix', 'isTopHybridSidebarFirst', 'isTopHybridHeaderFirst', 'isVerticalHybridHeaderFirst', `${code}\nreturn getSiderAndCollapsedWidth`)(themeStore, appStore, isVerticalMix, inactive, inactive, inactive)
  assert.equal(width(false), 360)
  assert.equal(width(true), 228)
  appStore.mixSiderFixed = false
  assert.equal(width(false), 180)
  assert.equal(width(true), 48)
  appStore.isMobile = true
  assert.equal(width(false), 220)
  assert.equal(width(true), 64)
  const menu = descriptor('vertical-mix-menu', 'layouts/modules/global-menu/modules')
  assert.match(menu.template.content, /:options="routeStore.menus"/)
  assert.match(menu.template.content, /:options="deviceMenus"/)
  assert.match(menu.template.content, /:value="deviceSelection.id"/)
  assert.match(menu.template.content, /v-model:expanded-keys="expandedTypes"/)
  assert.match(menu.template.content, /Math.max\(themeStore.sider.mixWidth, themeStore.sider.mixChildMenuWidth\)/)
  assert.match(menu.styles[0].content, /\.n-submenu-children/)
  assert.match(menu.styles[0].content, /\.n-submenu-children\)::before[^}]*z-index:1/)
  assert.match(menu.scriptSetup.content, /showDrawer = computed\(\(\) => drawerVisible.value \|\| appStore.mixSiderFixed\)/)
  assert.doesNotMatch(menu.scriptSetup.content, /if \(appStore.siderCollapse/)
  assert.match(menu.template.content, /:collapsed="appStore.siderCollapse"/)
  assert.match(descriptor('menu-toggler', 'components/common').template.content, /:tooltip-content="\(\$attrs\['aria-label'\]/)
  assert.match(layout.scriptSetup.content, /'vertical-mix': \{\s*showLogo: false,\s*showMenu: false,\s*showMenuToggler: true/)
  assert.match(descriptor('index', 'layouts/modules/global-menu').scriptSetup.content, /if \(appStore.isMobile\) return VerticalMenu/)
})

test('report form and history pagination retain horizontal text and consistent control heights', () => {
  const form = descriptor('ReportForm', 'components')
  const styles = form.styles[0].content
  assert.match(styles, /repeat\(auto-fit, minmax\(min\(100%, 150px\), 1fr\)\)/)
  assert.match(styles, /\.form-actions button[^}]*height:40px[^}]*white-space:nowrap/)
  assert.match(styles, /\.n-base-selection\)[^}]*\.n-input\)[^}]*--n-height:var\(--control-height\)!important/)
  const shared = readFileSync(new URL('../src/assets/style.css', import.meta.url), 'utf8')
  assert.match(shared, /--control-height:\s*40px;/)
  assert.match(shared, /\.history-page-size \{[^}]*flex-shrink:0[^}]*white-space:nowrap/)
  const history = descriptor('ReportTable', 'components')
  assert.match(history.template.content, /<AppReportTable[\s\S]*:loading="loading"/)
  assert.match(history.template.content, /loading \? '正在加载…' : '暂无历史报告'/)
  assert.match(history.styles[0].content, /\.history-wrap \{[^}]*padding:16px;/)
  assert.match(history.styles[0].content, /\.history-empty \{ color:var\(--text-color-3\);/)
  assert.match(history.styles[0].content, /\.actions-cell \{[^}]*justify-content:center;[^}]*gap:12px;/)
})

test('the supplied project logo is reused across primary product entry points', () => {
  const admin = descriptor('index', 'layouts/modules/global-logo').template.content
  const worker = descriptor('WorkerLayout', 'layouts').template.content
  const login = descriptor('LoginView').template.content
  const index = readFileSync(new URL('../index.html', import.meta.url), 'utf8')

  for (const view of [descriptor('index', 'layouts/modules/global-logo'), descriptor('WorkerLayout', 'layouts'), descriptor('LoginView')]) {
    assert.match(view.scriptSetup.content, /import projectLogo from '(?:\.\.\/)+assets\/project-logo\.png'/)
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
  assert.match(select.template.content, /<NSelect/)
  assert.match(select.template.content, /:required="required"/)
  assert.match(select.template.content, /@invalid.prevent="handleInvalid"/)
  assert.match(select.scriptSetup.content, /emit\('change', \{ target: \{ value \}, value \}\)/)

})

test('alarm severity colors agree and dispatch project names can display in full', () => {
  const alarm = descriptor('AlarmCenterView').styles[0].content
  const template = descriptor('AlarmCenterView').template.content
  assert.match(template, /<NTag class="severity-badge"[^>]*item.severity === 'critical' \? 'error' : item.severity === 'high' \? 'warning' : 'info'/)
  const dispatch = descriptor('CuttingDispatchView')
  assert.match(dispatch.template.content, /<NAutoComplete[^\n]*class="project-selector"[^\n]*maxWidth: 'calc\(100vw - 24px\)'/)
  assert.match(dispatch.styles[0].content, /:global\(\.dispatch-project-menu \.n-base-select-option__content\) \{ min-width:0; white-space:normal!important; overflow-wrap:anywhere;/)
  assert.match(dispatch.styles[0].content, /\.dispatch-workspace\.download-workspace \{ grid-template-columns:280px minmax\(0,1fr\)/)
  assert.match(dispatch.styles[0].content, /\.project-search :deep\(\.n-input\) \{ height:36px;/)
  assert.match(dispatch.styles[0].content, /\.project-label strong \{[^}]*white-space:normal;/)
  for (const name of ['project-folder', 'project-folder::before']) {
    assert.ok(dispatch.styles[0].content.includes(`.${name} { border-color:#d99a22; background:#f7c85b; }`))
  }
  const catalog = descriptor('CatalogTreeNode', 'components').styles[0].content
  for (const rule of catalog.matchAll(/\.catalog-folder(?:::before)? \{([^}]+)\}/g)) {
    assert.match(rule[1], /border:1px solid #d99a22;/)
    assert.match(rule[1], /background:#f7c85b;/)
  }
  const select = descriptor('AppSelect', 'components')
  assert.match(select.template.content, /:consistent-menu-width="consistentMenuWidth" :menu-props="\{ \.\.\.menuProps, id: menuId, role: 'listbox' \}"/)
  assert.match(select.scriptSetup.content, /consistentMenuWidth: true/)
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
  assert.match(picker.template.content, /<NDatePicker/)
  assert.match(picker.template.content, /:formatted-value="modelValue \|\| null"/)
  assert.match(picker.scriptSetup.content, /'yyyy-MM' : 'yyyy-MM-dd'/)
  assert.match(picker.template.content, /:is-date-disabled="isOutsideBounds"/)
  assert.match(picker.template.content, /:required="required"/)

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
  assert.match(period.template.content, /<NPopover :show="open"/)
  assert.match(period.scriptSetup.content, /单日/)
  assert.match(period.scriptSetup.content, /最近 7 天/)
  assert.match(period.scriptSetup.content, /跨月·近 30 天/)
  assert.match(period.scriptSetup.content, /本季度/)
  assert.match(period.scriptSetup.content, /上季度/)
  assert.match(period.template.content, /@click="applyPeriod"[^>]*>应用/)
  assert.match(period.scriptSetup.content, /inclusiveDays > props\.maxDays/)
})

test('report panels render loading, empty and failure states with their reason and recovery action', async () => {
  for (const [state, expected, role] of [
    [{ loading: true }, '正在读取报表数据', 'status'],
    [{ empty: true }, '当前条件下暂无数据', 'status'],
    [{ error: '源数据库不可用' }, '源数据库不可用', 'alert'],
  ]) {
    const html = await renderComponent('components/ReportTrendPanel', { number: '01', title: '生产趋势', ...state })
    assert.ok(html.includes('生产趋势'))
    assert.ok(html.includes(expected))
    assert.ok(html.includes(`role="${role}"`))
    if (!state.loading) assert.ok(html.includes('重新查询'))
  }
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
  assert.match(views[2].scriptSetup.content, /summarySubtitle, format,[\s\S]*} = useEnergyReportData\(/)
  assert.match(energyDataScript, /if \(value === null \|\| value === undefined \|\| !Number\.isFinite\(Number\(value\)\)\) return '--'/)
})

test('refreshing a populated report retains its chart and rows instead of showing the initial loading state', async () => {
  const html = await renderComponent('components/ReportTrendPanel', {
    number: '01', title: '生产趋势', loading: true, hasData: true, chartLabel: '每日产量',
  }, { chart: () => '现有图表', table: () => '现有明细' })
  for (const text of ['正在更新', '现有图表', '现有明细', '每日产量']) assert.ok(html.includes(text))
  assert.ok(!html.includes('正在读取报表数据'))
  assert.ok(!html.includes('aria-hidden="true"'))
})

test('report export feedback can retry the exact last request', () => {
  const exportSource = readFileSync(new URL('../src/composables/useReportExport.ts', import.meta.url), 'utf8')
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
  assert.match(analysis.template.content, /<ReportAnalysis[^>]*:analysis="analysis"/)
  assert.match(script, /const targetKey = requestKey\(\)[\s\S]*if \(!props\.ready \|\| !props\.reportDate\)/)
  assert.match(script, /if \(!props\.reportDate \|\| queryScope\(\) !== displayedScope\) \{[\s\S]*analysis\.value = null/)

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

test('home KPI and production ring show verified data instead of decorative placeholders', () => {
  const home = descriptor('AdminHomeView')
  const kpi = descriptor('HomeKpiCard', 'components')
  const template = home.template.content
  const script = home.scriptSetup.content

  assert.equal((template.match(/<HomeKpiCard /g) || []).length, 3)
  assert.match(template, /grid-cols-1 sm:grid-cols-2 xl:grid-cols-4/)
  assert.match(template, /aria-label="生产天数与能耗"/)
  assert.match(kpi.template.content, /<MetricSparkline[^>]+variant="bar"[^>]+:values="series"/)
  assert.match(kpi.template.content, /<NBadge dot[^>]+:processing="!loading && numericValue !== null && !app.reducedMotion"/)
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
  assert.match(template, /number="02"[\s\S]*title="工人工时汇总"/)
  assert.doesNotMatch(template, /<section class="panel workshop-panel"|class="panel worker-time-summary"/)
  assert.doesNotMatch(template, /所选区间暂无工人确认数据/)
  assert.match(template, /<AppReportTable :columns="workerTableColumns" :items="result.worker_summaries"/)
  assert.match(css, /\.panel, \.job-card\s*\{[^}]*margin-bottom: 22px;/)
  assert.doesNotMatch(css, /\.worker-time-summary\s*\{[^}]*margin:/)
  assert.match(trendStyles, /\.report-trend-panel__content--fit-table \.report-trend-panel__table\s*\{[^}]*height:auto;[^}]*max-height:none;/)
})

test('report and settings groups use sidebar navigation without duplicate page menus', () => {
  for (const name of ['ReportsLayout', 'SettingsLayout']) {
    const layout = descriptor(name, 'layouts')
    assert.match(layout.template.content, /<RouterView/)
    assert.doesNotMatch(layout.template.content, /<nav|<RouterLink|report-navigation/)
    assert.doesNotMatch(layout.scriptSetup.content, /RouterLink/)
  }
  const reports = descriptor('ReportsLayout', 'layouts')
  assert.match(reports.template.content, /<KeepAlive :max="3">/)
  assert.match(reports.template.content, /route.meta.keepAlive/)
})

test('device report is one page with task details and a default monthly summary', () => {
  const container = descriptor('AdminEquipmentView')
  const energy = descriptor('EnergyPeriodReportView')

  assert.match(container.template.content, /<EnergyPeriodReportView[\s\S]*@export="exportReport"/)
  assert.doesNotMatch(container.template.content, /EquipmentReportView|reportTab|KeepAlive/)
  assert.match(energy.template.content, /<StandardReportQuery/)
  assert.doesNotMatch(energy.template.content, /报表类型|任务生产报表|change-report-tab/)
  assert.match(descriptor('StandardReportQuery', 'components').template.content, /<span><AppIcon name="chart"[^>]*\/>图表类型<\/span>/)
  assert.match(energy.template.content, /<h2>设备报表<\/h2>/)
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

test('cutting report tables use the shared Naive table with fixed-height bodies and summaries', () => {
  const table = descriptor('AppReportTable', 'components')
  for (const name of ['LeadershipReportView', 'EnergyPeriodReportView']) {
    const view = descriptor(name)
    assert.equal((view.template.content.match(/<AppReportTable flex-height/g) || []).length, 2)
    assert.equal((view.template.content.match(/#summary-c0>汇总/g) || []).length, 2)
    assert.equal((view.template.content.match(/#summary-c1><\/template>/g) || []).length, 2)
    assert.doesNotMatch(view.scriptSetup.content, /title: [\"']备注[\"']/)
    assert.doesNotMatch(view.template.content, /row\.remark/)
    assert.doesNotMatch(view.template.content, /<table|<colgroup>/)
    assert.match(view.styles.map(style => style.content).join('\n'), /height:var\(--report-trend-height\)!important;\s*overflow:hidden!important;\s*scrollbar-gutter:auto;/)
  }
  assert.match(table.template.content, /<NDataTable[\s\S]*:summary="summary \? createSummary : undefined"/)
  assert.match(table.template.content, /:flex-height="flexHeight"/)
  assert.match(table.styles[0].content, /n-data-table-td--summary\)\{position:sticky;bottom:0;/)
})

test('report automation exposes secure live runtime configuration', async t => {
  const automation = descriptor('AutomationView')
  const template = automation.template.content
  const script = automation.scriptSetup.content

  assert.match(template, /运行与邮件参数/)
  assert.match(template, /即时生效/)
  assert.doesNotMatch(template, /需重启生效/)
  assert.match(template, /configuration\.smtp_password_configured/)
  assert.match(template, /@submit\.prevent="submitConfiguration"/)
  assert.match(readFileSync(new URL('../src/composables/useAutomationConfiguration.ts', import.meta.url), 'utf8'), /smtp_password: configuration\.smtp_password \|\| null/)
  assert.match(readFileSync(new URL('../src/composables/useAutomationConfiguration.ts', import.meta.url), 'utf8'), /configurationMessage\.value = result\.message[\s\S]*?await refresh\(\)/)
  assert.match(script, /Promise\.all\(\[loadConfiguration\(\), loadAll\(\)\]\)/)
  const calls = []
  const result = { saved: true, restart_required: false, message: '已生效' }
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options })
    return new Response(JSON.stringify({ success: true, data: result }), { headers: { 'Content-Type': 'application/json' } })
  })
  assert.deepEqual(await getAutomationConfiguration(), result)
  const payload = { automation_enabled: true, smtp_host: 'smtp.example.com', smtp_password: null }
  assert.deepEqual(await saveAutomationConfiguration(payload), result)
  assert.deepEqual(calls.map(call => new URL(call.url, 'http://test').pathname), Array(2).fill('/api/v1/automation/configuration'))
  assert.equal(calls[1].options.method, 'PUT')
  assert.deepEqual(JSON.parse(calls[1].options.body), payload)
  assert.ok(calls.every(call => call.options.credentials === 'same-origin'))
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
  assert.match(template, /formatShortDateTime\(item\.first_start_time\)/)
  assert.match(template, /同步游标[\s\S]*ALARM_INFO_ID/)
  assert.doesNotMatch(template, /已同步记录/)
  assert.match(script, /const startValue = item\?\.first_start_time/)
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

test('alarm current-status filter queries every open incident instead of filtering one page', () => {
  const alarm = descriptor('AlarmCenterView')
  const template = alarm.template.content
  const script = alarm.scriptSetup.content

  assert.match(script, /const onlyOpen = ref\(false\)/)
  assert.match(script, /onlyOpen\.value = !onlyOpen\.value[\s\S]*incidentPage\.value = 1[\s\S]*incidents\.value = \[\][\s\S]*await loadData\(\)/)
  assert.match(script, /listAlarmIncidentPage\(\{[\s\S]*status: onlyOpen\.value \? 'open' : ''/)
  assert.match(template, /class="alarm-status-filter"[^>]*:aria-pressed="onlyOpen"[^>]*@click="toggleOpenFilter"/)
  assert.match(template, /当前异常（\{\{ openCount \}\}）/)
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

test('all standard reports share the leadership analysis structure and compact colored style', () => {
  for (const name of ['LeadershipReportView', 'WorkshopView', 'EnergyPeriodReportView']) {
    const template = descriptor(name).template.content
    assert.match(template, /<AnalysisPanel/)
    assert.doesNotMatch(template, /(?:leadership|workshop|device)-analysis/)
  }

  const analysis = descriptor('ReportAnalysis', 'components')
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
  assert.match(styles, /\.analysis-panel\{[^}]*background:rgb\(var\(--container-bg-color\)\);/)
  assert.match(styles, /\.analysis-grid article:nth-child\(3\)\{[^}]*background:rgb\(var\(--error-color\) \/ \.1\)!important;?/)
  assert.match(styles, /\.analysis-grid article:nth-child\(4\)\{[^}]*background:rgb\(var\(--success-color\) \/ \.1\)!important;?/)
  assert.match(styles, /\.analysis-panel-head\{[^}]*padding:16px!important;/)
  assert.match(styles, /@media\(max-width:800px\)[\s\S]*\.analysis-grid\{[^}]*grid-template-columns:1fr!important;?/)
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

test('leadership and equipment second tables default to the recent twelve months', () => {
  const leadership = descriptor('LeadershipReportView')
  const equipment = descriptor('EnergyPeriodReportView')
  const leadershipRange = leadershipQueryScript.slice(leadershipQueryScript.indexOf('async function loadReport'), leadershipQueryScript.indexOf('async function restoreRouteState'))
  const equipmentRange = equipment.scriptSetup.content.match(/async function loadRange[\s\S]*?\n}\n\nfunction stopSummaryRefresh/)?.[0] || ''

  assert.match(leadershipQueryScript, /monthlyPeriodStart = ref\(recentTwelveMonthStart\(today\)\)/)
  assert.match(leadershipQueryScript, /const monthlyReport = ref<(?:LeadershipReport|EnergyPeriodReport) \| null>\(null\)/)
  assert.match(leadershipDataScript, /monthlyRecords = computed\(\(\) => monthlyReport\.value\?\.monthly_records/)
  assert.doesNotMatch(leadershipRange, /monthlyReport\.value|monthlyPeriodStart\.value|monthlyPeriodEnd\.value/)
  assert.match(leadershipRange, /renderCharts\(\{ monthly: false \}\)/)
  assert.match(leadership.template.content, /:title="monthlyTitle"/)
  assert.match(leadership.template.content, /:note="`\$\{monthlyPeriodStart\} 至 \$\{monthlyPeriodEnd\}`"/)
  assert.match(leadership.scriptSetup.content, /summary_period_start: monthlyPeriodStart\.value/)
  assert.match(leadership.scriptSetup.content, /summary_period_end: monthlyPeriodEnd\.value/)
  assert.match(energyDataScript, /summaryRows = computed\(\(\) => sortReportPeriods\(monthlyReport\.value\?\.rows/)
  assert.match(equipment.template.content, /:title="summaryTitle"/)
  assert.match(equipment.template.content, /:subtitle="summarySubtitle"/)
  assert.match(energyDataScript, /后台补齐 \$\{missing\} 天能耗中/)
  assert.match(equipment.scriptSetup.content, /summary_period_start: summaryPeriodStart\.value/)
  assert.match(equipment.scriptSetup.content, /summary_period_end: summaryPeriodEnd\.value/)
})

test('switching cached workshop and equipment reports does not force another query', () => {
  const workshop = descriptor('WorkshopView')
  const equipment = descriptor('EnergyPeriodReportView')
  const preload = readFileSync(new URL('../src/state/reportPreload.ts', import.meta.url), 'utf8')
  const api = readFileSync(new URL('../src/api/cutting.ts', import.meta.url), 'utf8')

  assert.match(workshop.scriptSetup.content, /force = false/)
  assert.match(workshop.scriptSetup.content, /if \(!pageActive\.value \|\| !isWorker\.value \|\| refreshTimer\) return/)
  assert.match(workshop.scriptSetup.content, /if \(isWorker\.value && result\.value\.report/)
  assert.doesNotMatch(equipment.scriptSetup.content, /onActivated\([\s\S]{0,300}getEnergyRange/)
  assert.match(preload, /listWorkshopRangeTasks\(\{ startDate: reportDate, endDate: reportDate \}\)/)
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
  assert.match(analysis, /if \(!props\.reportDate \|\| queryScope\(\) !== displayedScope\) \{[\s\S]*analysis\.value = null/)
  assert.match(template, /:page="result.page"[^>]*:page-size="result.page_size"/)
  assert.match(template, /@page="changePage"/)
})

test('cutting list pagination is rendered once by the shared Naive component', () => {
  for (const name of ['WorkshopView', 'AlarmCenterView', 'CuttingDispatchView']) {
    const template = descriptor(name).template.content
    assert.match(template, /<AppReportTable/)
    assert.match(template, /:page-size=/)
    assert.match(template, /@page=/)
    assert.doesNotMatch(template, />上一页<|>下一页</)
  }
  const report = descriptor('ReportTable', 'components').template.content
  assert.match(report, /@page="emit\('page-change', \$event\)"/)
  assert.match(report, /@page-size="emit\('page-size-change', \$event\)"/)
  const shared = descriptor('AppReportTable', 'components').template.content
  assert.equal((shared.match(/<NPagination/g) || []).length, 1)
  assert.match(shared, /:item-count="total"/)
  assert.match(shared, /:disabled="loading"/)
})

test('report charts resize with their containers and disconnect observers while inactive', async () => {
  for (const name of ['LeadershipReportView', 'EnergyPeriodReportView']) {
    const source = descriptor(name).scriptSetup.content
    const chartSource = name === 'EnergyPeriodReportView' ? energyChartsScript : leadershipChartsScript
    const code = chartSource.slice(chartSource.indexOf('function resizeCharts()'), chartSource.indexOf('function chartsMissing()'))
    const observed = [], observers = [], calls = []
    const chart = { resize: () => calls.push('resize') }
    const panel = { value: { getChartElement: () => ({}) } }
    const pageActive = { value: true }
    class ResizeObserver {
      constructor(callback) { this.callback = callback; observers.push(this) }
      observe(element) { observed.push(element) }
      disconnect() { this.disconnected = true }
    }
    const bindings = { ResizeObserver, pageActive, dailyPanel: panel, monthlyPanel: panel, taskPanel: panel, periodPanel: panel,
      dailyChart: chart, monthlyChart: chart, taskChart: chart, periodChart: chart,
      window: { addEventListener() {}, removeEventListener() {} } }
    const state = scriptFunction(...Object.keys(bindings), `let resizeListening = false; let chartResizeObserver; ${code}; return { startResizeListening, stopResizeListening }`)(...Object.values(bindings))
    state.startResizeListening()
    state.startResizeListening()
    assert.equal(observers.length, 1)
    assert.equal(observed.length, 2)
    observers[0].callback()
    assert.equal(calls.length, 2)
    pageActive.value = false
    observers[0].callback()
    assert.equal(calls.length, 2)
    state.stopResizeListening()
    assert.equal(observers[0].disconnected, true)
    state.startResizeListening()
    assert.equal(observers.length, 2)
    state.stopResizeListening()

    panel.value = null
    const before = observed.length
    let activate
    const activation = source.slice(source.indexOf('onActivated(async () => {'), source.indexOf('onDeactivated('))
    const activationBindings = { ...bindings,
      onActivated: callback => { activate = callback },
      nextTick: async () => { panel.value = { getChartElement: () => ({}) } },
      routeStateReady: false, restoreRouteState: async () => false,
      report: { value: {} }, taskDay: { value: {} }, monthlyReport: { value: {} }, loading: { value: false },
      scheduleRefreshPoll() {}, scheduleSummaryRefresh() {}, renderCharts() {}, chartsMissing: () => false }
    const active = scriptFunction(...Object.keys(activationBindings), `let resizeListening = false; let chartResizeObserver; ${code}; ${activation}; return { stopResizeListening }`)(...Object.values(activationBindings))
    pageActive.value = true
    await activate()
    assert.equal(observed.length, before + 2, 'activation must observe the chart elements after Vue restores their refs')
    active.stopResizeListening()
  }
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

test('long-lived page polling is serialized and cancelled when the page leaves', () => {
  const alarm = descriptor('AlarmCenterView').scriptSetup.content
  const workshop = descriptor('WorkshopView').scriptSetup.content
  const reportCenter = descriptor('ReportCenterView').scriptSetup.content
  const api = readFileSync(new URL('../src/api/alarms.ts', import.meta.url), 'utf8')

  assert.doesNotMatch(alarm, /setInterval\(loadData/)
  assert.match(alarm, /if \(!pageActive \|\| requestController\) return/)
  assert.match(alarm, /requestController\?\.abort\(\)/)
  assert.match(alarm, /await loadData\(\)[\s\S]*scheduleRefresh\(\)/)
  assert.match(api, /getAlarmStatus\(\{ signal \}: ReadOptions = \{\}\)/)
  assert.match(api, /listAlarmIncidentPage\([\s\S]*\{ signal \}: ReadOptions = \{\}\)/)
  assert.match(api, /listAlarmReports\([\s\S]*\{ signal \}: ReadOptions = \{\}\)/)
  assert.match(api, /listAlarmReportPage\([\s\S]*\{ signal \}: ReadOptions = \{\}\)/)

  assert.doesNotMatch(workshop, /setInterval\(/)
  assert.match(workshop, /refreshTimer = window\.setTimeout\(async \(\) =>/)
  assert.match(workshop, /await load\(result\.value\.page,[\s\S]*startRefreshTimer\(\)/)

  assert.match(reportCenter, /function stopPolling\(\)[\s\S]*pollController\?\.abort\(\)/)
  assert.match(reportCenter, /getReportStatus\(reportId, \{ signal: controller\.signal \}\)/)
  assert.match(reportCenter, /if \(!pageActive \|\| controller\.signal\.aborted \|\| sequence !== pollSequence\) return/)
  assert.match(reportCenter, /listReports\(\{ page, pageSize, force, signal: controller\.signal \}\)/)
  assert.match(reportCenter, /sequence !== historySequence/)
  assert.match(reportCenter, /onBeforeUnmount\(\(\) => \{[\s\S]*pageActive = false[\s\S]*stopPolling\(\)/)
})

test('energy rows show numeric values without an extra explanation column and preserve missing readings', async () => {
  const empty = { render: () => null }
  const tables = []
  const records = ['measured', 'interpolated', 'estimated', 'missing', 'counter_reset', 'mock'].map((status, index) => ({
    task_id: String(index), gas_data_status: status, electricity_data_status: 'measured',
    electricity_consumption: 27.4,
    gas_consumption: ['missing', 'counter_reset'].includes(status) ? null : index === 0 ? 0 : 1,
    energy_note: status === 'estimated' ? '平均速率补齐；有效时段 08:00～08:30（覆盖1800秒 / 任务3600秒）' : status,
  }))
  const bindings = { ...Vue, useEnergyReportData, formatChineseDateTime: value => value || '--',
    useEnergyReportQuery: () => Object.fromEntries(Object.entries({ taskDay: { records }, monthlyReport: null,
      anchorDate: '2026-11-07', periodStart: '2026-11-07', periodEnd: '2026-11-07',
      appliedPeriodStart: '2026-11-07', appliedPeriodEnd: '2026-11-07', summaryPeriodStart: '2025-12-01', summaryPeriodEnd: '2026-11-07',
      loading: false, summaryLoading: false, error: '月度统计不可用', chartType: 'bar_line',
    }).map(([key, value]) => [key, Vue.ref(value)])),
    useEnergyReportCharts: () => ({ stopResizeListening() {}, disposeCharts() {} }), AnalysisPanel: empty, AppIcon: empty, MetricSparkline: empty, StandardReportQuery: empty,
    ReportFeedback: { props: ['title', 'message'], setup: props => () => Vue.h('p', `${props.title} ${props.message}`) },
    ReportTrendPanel: { inheritAttrs: false, setup: (_props, { slots }) => () => Vue.h('section', slots.table?.()) },
    AppReportTable: { props: ['items', 'columns'], setup: (props, { slots }) => {
      tables.push(props.columns)
      return () => Vue.h('table', props.items.map((row, index) => Vue.h('tr', props.columns.map(column => Vue.h('td', slots[column.key]?.({ row, index }))))))
    } },
  }
  const code = compileScript(descriptor('EnergyPeriodReportView'), { id: 'energy-status-display', inlineTemplate: true }).content
    .replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, names) => {
      for (const name of names.split(',')) { const [original, alias = original] = name.trim().split(/\s+as\s+/); bindings[alias] = Vue[original] }
      return ''
    }).replace(/^import .*$/gm, '').replace('export default', 'return')
  const Page = scriptFunction(...Object.keys(bindings), code)(...Object.values(bindings))
  const app = Vue.createSSRApp(Page)
  app.component('NButton', { setup: (_props, { attrs, slots }) => () => Vue.h('button', attrs, slots.default?.()) })
  const html = await renderToString(app)
  assert.deepEqual(tables[0].map(column => column.title), ['序号', '工程号', '船名', '分段名', '任务开始', '切割时长', '电耗', '气耗'])
  assert.equal((html.match(/<span title="[^"]*">27\.4<\/span>/g) || []).length, records.length)
  assert.match(html, /<span title="measured">0\.0<\/span>/)
  assert.match(html, /<span title="interpolated">1\.0<\/span>/)
  assert.match(html, /<span title="平均速率补齐；有效时段 08:00～08:30（覆盖1800秒 \/ 任务3600秒）">1\.0<\/span>/)
  assert.match(html, /<span title="missing">--<\/span>/)
  assert.match(html, /<span title="counter_reset">--<\/span>/)
  assert.doesNotMatch(html, /能耗依据|按有效任务汇总|气：/)
  assert.ok(html.includes('设备报表更新失败 月度统计不可用；当前保留已成功加载的数据。'))
})

test('scrollable report tables do not add duplicate previous and next pagination', () => {
  const energy = descriptor('EnergyPeriodReportView')
  const script = energy.scriptSetup.content
  const template = energy.template.content

  assert.match(template, /:items="taskRows"/)
  assert.doesNotMatch(template, /#footer|上一页|下一页/)
  assert.doesNotMatch(script, /visibleTaskRows|totalPages|pageSize/)
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

test('home alarm and report previews share a bounded desktop height and insight spacing stays balanced', () => {
  const template = descriptor('AdminHomeView').template.content
  for (const title of ['alarm', 'reports']) {
    const card = template.match(new RegExp(`<NCard[^>]+aria-labelledby="${title}-card-title"`))[0]
    assert.match(card, /xl:max-h-300px/)
    assert.match(card, /content-class="min-h-0 overflow-hidden"/)
    assert.doesNotMatch(card, /self-start/)
  }
  assert.match(template, /incidents.slice\(0, 3\)/)
  assert.match(template, /key="alarms" class="flex-col-stretch gap-8px"/)
  assert.match(template, /key="reports" class="flex-col-stretch gap-8px"/)
  assert.equal((template.match(/class="h-72px min-w-0/g) || []).length, 2)
  for (const route of ['/admin/alarms', '/admin/reports/center']) assert.ok(template.includes(`to="${route}"`))
  const insight = template.match(/<NCard[^>]+aria-labelledby="insights-card-title"/)[0]
  assert.match(insight, /header-style="padding-bottom: 0"/)
  assert.match(insight, /content-style="padding-top: var\(--n-padding-bottom\)"/)
  assert.match(insight, /content-class="flex flex-col justify-center"/)
})
