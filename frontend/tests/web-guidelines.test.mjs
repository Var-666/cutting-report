import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

function source(path) {
  return readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
}

test('application shells expose skip links and live authentication feedback', () => {
  const app = source('App.vue')
  const admin = source('layouts/AdminLayout.vue')
  const worker = source('layouts/WorkerLayout.vue')
  assert.match(app, /role="status" aria-live="polite">正在检查登录状态…/)
  assert.match(app, /class="auth-loading auth-failed" role="alert"/)
  assert.match(admin, /class="skip-link" href="#admin-main"/)
  assert.match(admin, /scroll-el-id="admin-main"/)
  assert.match(worker, /class="skip-link" href="#worker-main"/)
  assert.match(worker, /scroll-el-id="worker-main"/)
  assert.match(source('vendor/soybean-admin/index.vue'), /<main[\s\S]*?tabindex="-1"/)
})

test('critical forms have names, inline alerts, and unsaved-change protection', () => {
  const login = source('views/LoginView.vue')
  const database = source('views/DatabaseView.vue')
  const automation = source('views/AutomationView.vue')
  const designer = source('views/DesignerView.vue')
  assert.doesNotMatch(login, /<h2>/)
  assert.match(login, /name="username"[\s\S]*spellcheck: false/)
  assert.match(login, /class="login-error" role="alert" tabindex="-1"/)
  assert.match(database, /useUnsavedChanges\(hasUnsavedChanges/)
  assert.match(database, /class="inline-result error" role="alert"/)
  assert.match(automation, /useUnsavedChanges\(computed\(\(\) => hasUnsavedChanges\.value \|\| resolutionDirty\.value\)/)
  assert.match(automation, /name="smtp_from_email"[\s\S]*inputmode="email"/)
  assert.match(designer, /useUnsavedChanges\(hasUnsavedChanges/)
  assert.match(designer, /role="tab"[\s\S]*:aria-selected=/)
  assert.match(designer, /role="tabpanel"/)
})

test('modal workflows trap focus and data grids label editable controls', () => {
  const accounts = source('views/AdminAccountsView.vue')
  const dispatch = source('views/CuttingDispatchView.vue')
  const workshop = source('views/WorkshopView.vue')
  const dialogFocus = source('composables/useDialogFocus.ts')
  assert.match(dialogFocus, /event\.key === 'Escape'/)
  assert.match(dialogFocus, /event\.key !== 'Tab'/)
  for (const view of [accounts, dispatch, workshop]) {
    assert.match(view, /<NModal :show=/)
    assert.match(view, /:close-on-esc="!\w+\.saving"/)
    assert.doesNotMatch(view, /useDialogFocus\(/)
  }
  assert.match(dispatch, /:aria-label="`\$\{item\.filename\} 材质`"/)
  assert.match(dispatch, /:aria-label="`\$\{item\.filename\} 板厚`"/)
  assert.match(workshop, /class="confirmation-dialog-error" role="alert"/)
})

test('global interaction styles preserve visible focus and touch behavior', () => {
  const css = source('assets/style.css')
  const period = source('components/ReportPeriodPicker.vue')
  assert.doesNotMatch(css, /outline\s*:\s*none/)
  assert.match(css, /input:focus-visible/)
  assert.match(css, /touch-action:manipulation/)
  assert.match(css, /overscroll-behavior:contain/)
  assert.match(css, /env\(safe-area-inset-top\)/)
  assert.match(period, /:aria-labelledby="`\$\{labelId\} \$\{valueId\}`"/)
})


test('welding home offers equivalent chart tables and mobile welding tables release both fixed sides', () => {
  const home = source('views/WeldingHomeView.vue')
  for (const title of ['查看生产趋势数据', '查看工艺结构数据', '查看设备产出数据']) assert.ok(home.includes(`<summary>${title}</summary>`))
  assert.match(home, /v-for="row in daily"[\s\S]*weldingDisplay\(row.weld_length_m, 'weld_length_m'\)[\s\S]*weldingDisplay\(row.job_count, 'job_count'\)/)
  assert.match(home, /v-for="row in processes"[\s\S]*weldingDisplay\(row.length, 'weld_length_m'\)/)
  assert.match(home, /v-for="row in devices"[\s\S]*weldingDisplay\(row.weld_length_m, 'weld_length_m'\)/)
  assert.equal((home.match(/<caption>/g) || []).length, 3)
  assert.equal((home.match(/scope="row"/g) || []).length, 3)
  const table = source('components/AppReportTable.vue')
  assert.match(table, /@media\(max-width:600px\)[\s\S]*welding-table-wrap :deep\(\.n-data-table-td--fixed-left\)[^{]*\{position:static!important\}/)
  assert.doesNotMatch(table, /not\(\.welding-table-wrap\)/)
  assert.match(source('components/HomeKpiCard.vue'), /formattedValue} \$\{unit}/)
})
