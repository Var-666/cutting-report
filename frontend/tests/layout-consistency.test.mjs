import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import { parse } from '@vue/compiler-sfc'

function descriptor(name, folder = 'views') {
  const source = readFileSync(new URL(`../src/${folder}/${name}.vue`, import.meta.url), 'utf8')
  return parse(source).descriptor
}

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
  assert.match(energy.template.content, /<section class="equipment-hero">/)
  assert.match(energy.template.content, /class="equipment-query energy-period-query"/)
  assert.doesNotMatch(energy.template.content, /报表类型|任务生产报表|change-report-tab/)
  assert.match(energy.template.content, /<h2>设备报表<\/h2>/)
  assert.match(energy.template.content, /任务能耗明细/)
  assert.match(energy.template.content, /每行一个任务/)
  assert.match(energy.template.content, /按开始时间由近到远/)
  assert.match(energy.template.content, /<tfoot>[\s\S]*当日任务汇总/)
  assert.match(energy.template.content, /月度每日能耗汇总/)
  assert.match(energy.template.content, /aria-label="任务能耗明细表"/)
  assert.match(energy.template.content, /aria-label="月度每日能耗汇总表"/)
  assert.doesNotMatch(energy.template.content, /任务结束/)
  assert.doesNotMatch(energy.template.content, /数据状态/)
  assert.match(energy.template.content, /colspan="8" class="energy-empty"/)
  assert.match(energy.template.content, /scope="row" colspan="5">当日任务汇总/)
  assert.match(energy.scriptSetup.content, /import AnalysisPanel from '\.\.\/components\/AnalysisPanel\.vue'/)
  assert.match(energy.template.content, /<AnalysisPanel[\s\S]*report-type="equipment"/)
  assert.match(energy.template.content, /:ready="analysisReady"/)
  assert.equal((energy.template.content.match(/class="energy-period-split"/g) || []).length, 2)
})

test('file push uses downloaded-file parameters without a condition search', () => {
  const dispatch = descriptor('CuttingDispatchView')

  assert.match(dispatch.template.content, /<section v-if="isDownload" class="panel dispatch-query-panel">/)
  assert.match(dispatch.template.content, /选择已下载文件/)
  assert.match(dispatch.scriptSetup.content, /listCuttingDownloads\(\{ page: 1, pageSize: 100 \}\)/)
  assert.match(dispatch.scriptSetup.content, /download_id: activeFile\.value\.download_id/)
  assert.match(dispatch.template.content, /taskName 与实际上传文件名一致/)
  assert.match(dispatch.template.content, /PDM_DesignDocumentInfo \/ PDM_NCDocumentCatalog \/ PDM_DocumentFileInfo/)
})
