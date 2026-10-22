import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { compactAnalysisFactLabel, compactAnalysisFactValue } from '../src/utils/analysisFactDisplay.js'
import { formatDeviceDisplay } from '../src/utils/deviceDisplay.js'

const source = readFileSync(new URL('../src/components/AnalysisPanel.vue', import.meta.url), 'utf8')

test('report model text uses the same device alias without changing numeric formatting or evidence', () => {
  const formatter = source.slice(source.indexOf('function formatAnalysisText('), source.indexOf('function plainAnalysisText('))
  const format = new Function('formatDeviceDisplay', `${formatter}; return formatAnalysisText`)(formatDeviceDisplay)
  assert.equal(format('Cutter01电耗 1,234.567 kWh [F001]'), '20KW切割机电耗 1,234.6 kWh [F001]')
  assert.equal(format('Cutter02电耗 12.345 kWh'), '30KW切割机电耗 12.3 kWh')
})

test('analysis text is visible by default without a collapsed-card action', () => {
  assert.doesNotMatch(source, /class="analysis-expand"|isLongSection\(|isSectionExpanded\(/)
  assert.match(source, /\.analysis-grid h4,\.analysis-grid p\{display:block;overflow:visible;-webkit-line-clamp:unset;overflow-wrap:anywhere\}/)
  assert.match(source, /\.analysis-facts span:not\(\.analysis-fact-icon\),\.analysis-facts strong\{overflow:visible;text-overflow:clip;white-space:normal;overflow-wrap:anywhere\}/)
  assert.match(source, /\.analysis-facts article\{overflow:visible\}/)
})

test('single-sentence analysis is not repeated as both heading and body', () => {
  assert.match(source, /<p v-if="sectionBody\(analysis\[section\.key\]\)"/)
  assert.doesNotMatch(source, /sectionBody\(analysis\[section\.key\]\) \|\| plainAnalysisText/)
  assert.match(source, /if \(firstBreak < 0 \|\| firstBreak >= text\.length - 1\) return ''/)
})

test('model-only analysis hides reference codes but keeps evidence available', () => {
  assert.doesNotMatch(source, /<sup v-for="citation in sectionCitations/)
  assert.match(source, /查看模型输入数据/)
  assert.match(source, /AI 分析/)
  assert.match(source, /AI 生成/)
  assert.doesNotMatch(source, /根据报表生成|报表数据分析|降级分析/)
})

test('analysis checks for newer report data and asks the current report to sync automatically', () => {
  const api = readFileSync(new URL('../src/api/reports.js', import.meta.url), 'utf8')
  assert.match(api, /error\.status = response\.status/)
  assert.match(api, /export function getAnalysisVersion[\s\S]*\/api\/v1\/analysis\/version/)
  assert.match(source, /SOURCE_CHECK_INTERVAL_MS = 300_000/)
  assert.match(source, /async function revalidateSource\(\)/)
  const revalidateSource = source.match(/async function revalidateSource\(\)[\s\S]*?\n}\n\nasync function loadAnalysis/)[0]
  assert.match(revalidateSource, /getAnalysisVersion\(/)
  assert.doesNotMatch(revalidateSource, /analyzeReport\(/)
  assert.match(revalidateSource, /!props\.versionCheckEnabled/)
  assert.match(revalidateSource, /refreshed\?\.data_version[\s\S]*refreshed\.data_version !== analysisDataVersion\.value/)
  assert.match(source, /exception\.status === 409\) requestReportRefresh\(targetKey\)/)
  assert.match(source, /emit\('refresh-report'\)/)
  assert.match(source, /sourceCheckController\?\.abort\(\)/)
  assert.match(source, /window\.clearInterval\(sourceCheckTimer\)/)
  assert.match(source, /if \(!analysis\.value\) \{[\s\S]*refreshRequestedKey = ''[\s\S]*requestReportRefresh\(targetKey\)/)
  for (const name of ['LeadershipReportView', 'EnergyPeriodReportView', 'WorkshopView']) {
    const view = readFileSync(new URL(`../src/views/${name}.vue`, import.meta.url), 'utf8')
    assert.match(view, /@refresh-report="refreshAnalysisReport"/)
    assert.match(view, /:version-check-enabled=/)
    assert.match(view, /force: true|loadReport\(true\)|loadRange\(true\)/)
  }
  const workshop = readFileSync(new URL('../src/views/WorkshopView.vue', import.meta.url), 'utf8')
  assert.match(workshop, /const analysisReady = computed\([\s\S]*&& !loading\.value/)
})

test('core metric cards omit technical source labels while evidence keeps traceability', () => {
  assert.doesNotMatch(source, /<small>\{\{ fact\.source \|\| '当前报表数据' \}\}<\/small>/)
  assert.match(source, /<small>来源：\{\{ fact\.source \|\| '当前报表数据' \}\}<\/small>/)
  assert.match(source, /\.analysis-facts article\{[^}]*min-height:104px[^}]*align-items:center/)
  assert.match(source, /\.analysis-facts strong\{[^}]*font-size:18px!important/)
  assert.match(source, /compactAnalysisFactValue\(formatAnalysisText\(fact\.value\)\)/)
  assert.match(source, /compactAnalysisFactLabel\(fact\.label, fact\.value\)/)
  assert.match(source, /<span>\{\{ formatAnalysisText\(fact\.value\) \}\}<\/span>/)
})

test('compound fact values stay compact in metric cards while the detail remains available', () => {
  assert.equal(compactAnalysisFactValue('工程号未填写 984 项，任务较多的日期 1 天'), '工程号未填写 984 项')
  assert.equal(compactAnalysisFactValue('5 项待确认，比上一周期下降 5.6%'), '5 项待确认')
  assert.equal(compactAnalysisFactValue('18,708.5 kWh'), '18,708.5 kWh')
  assert.equal(compactAnalysisFactValue('缺失'), '缺失')
  assert.equal(compactAnalysisFactLabel('工程号与任务较多的日期', '工程号未填写 984 项，任务较多的日期 1 天'), '工程号')
  assert.equal(compactAnalysisFactLabel('待确认任务与数量变化', '5 项待确认，比上一周期下降 5.6%'), '待确认任务')
  assert.equal(compactAnalysisFactLabel('已匹配任务电耗', '18,708.5 kWh'), '已匹配任务电耗')
})
