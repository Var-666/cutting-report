<script setup lang="ts">
import type { ConnectionTestResult, DatabaseConfiguration, DatabaseSource, DatabaseStatus, SaveResult, SqlConnection, WeldingConnection } from '../types/configuration'

import { toError } from '../utils/errors'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  getDatabaseConfiguration,
  getDatabaseStatus,
  saveDatabaseConfiguration,
  testDatabaseConnection,
  testWeldingConnection,
  saveWeldingConfiguration,
} from '../api/reports'
import { deviceSelection, loadDevices } from '../state/devices.ts'
import { formatTableDateValue } from '../utils/dateDisplay.ts'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.ts'

const status = ref<DatabaseStatus | null>(null)
const statusError = ref('')
const databaseConfiguration = ref<DatabaseConfiguration | null>(null)
const configurationLoading = ref(false)
const configurationSaving = ref(false)
const configurationError = ref('')
const configurationResult = ref<SaveResult | null>(null)
const testing = ref(false)
const testResult = ref<ConnectionTestResult | null>(null)
const testError = ref('')
const weldingScope = computed(() => deviceSelection.id === 'welding')
const testSource = ref('sqlserver')
const testIsWelding = computed(() => weldingScope.value || testSource.value === 'welding')
const dataSources = computed(() => (status.value?.data_sources || []).filter(source => !weldingScope.value || source.source === 'welding'))
const connectionStatus = computed(() => weldingScope.value
  ? databaseConfiguration.value?.welding?.password_configured ? '焊接参数已配置' : '等待配置'
  : status.value?.connection_configured ? '只读连接已配置' : '等待配置')
const hasUnsavedChanges = ref(false)
useUnsavedChanges(hasUnsavedChanges, '数据库连接参数还有未保存的修改，确定离开吗？')
const configurationSources: { key: DatabaseSource; label: string; description: string }[] = [
  { key: 'production', label: '生产报表库', description: 'SQLSERVER_* · dbo.task 等生产数据' },
  { key: 'worker', label: '工人 HR 库', description: 'WORKER_SQLSERVER_* · dbo.HRbase_userInfo' },
  { key: 'file', label: '切割文件库', description: 'FILE_SQLSERVER_* · PDM 文件索引/内容' },
]

const connection = reactive({
  host: '',
  port: 1433,
  database: '',
  username: '',
  password: '',
  driver: 'ODBC Driver 18 for SQL Server',
  encrypt: true,
  trust_server_certificate: true,
})
const weldingConnection = reactive({ host: '', port: 3306, database: 'weld_production_data', username: '', password: '', timezone: 'Asia/Shanghai' })
const activeTestConnection = computed(() => testIsWelding.value ? weldingConnection : connection)
watch(testIsWelding, () => { testResult.value = null; testError.value = '' })

const sourceLabels: Record<string, string> = { demo: '演示数据', sqlserver: 'SQL Server' }
const sourceName = computed(() => (sourceLabels[status.value?.mode || ''] || '正在检查') )
const sourceDescription = computed(() => {
  if (status.value?.mode === 'sqlserver') return '生产查询模式'
  return '尚未连接生产库'
})
function formatCell(value: unknown) {
  if (value === null || value === undefined || value === '') return '--'
  if (typeof value === 'number') return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(value)
  return formatTableDateValue(value)
}

async function loadStatus() {
  statusError.value = ''
  try {
    status.value = await getDatabaseStatus()
    connection.driver = status.value.driver
    connection.host = status.value.configured_host || ''
    connection.database = status.value.configured_database || ''
  } catch (caught) {
    const exception = toError(caught)
    statusError.value = exception.message
  }
}

async function loadConfiguration() {
  configurationLoading.value = true
  configurationError.value = ''
  try {
    databaseConfiguration.value = await getDatabaseConfiguration()
    for (const source of configurationSources) {
      databaseConfiguration.value[source.key].password = ''
    }
    if (databaseConfiguration.value.welding) {
      databaseConfiguration.value.welding.password = ''
      for (const key of Object.keys(weldingConnection)) Object.assign(weldingConnection, { [key]: databaseConfiguration.value.welding[key as keyof WeldingConnection] })
    }
    hasUnsavedChanges.value = false
  } catch (caught) {
    const exception = toError(caught)
    configurationError.value = exception.message
  } finally {
    configurationLoading.value = false
  }
}

async function saveConfiguration(weldingOnly = false) {
  configurationSaving.value = true
  configurationError.value = ''
  configurationResult.value = null
  try {
    if (!databaseConfiguration.value) return
    const payload: DatabaseConfiguration = {
      production: databaseConfiguration.value.production, worker: databaseConfiguration.value.worker, file: databaseConfiguration.value.file,
      data_source_mode: databaseConfiguration.value.data_source_mode,
      worker_database_auth_enabled: databaseConfiguration.value.worker_database_auth_enabled,
      file_database_enabled: databaseConfiguration.value.file_database_enabled,
      welding: { ...databaseConfiguration.value.welding, password: databaseConfiguration.value.welding?.password || null },
    }
    for (const source of configurationSources) {
      const value = databaseConfiguration.value[source.key]
      payload[source.key] = {
        host: value.host,
        port: value.port,
        database: value.database,
        username: value.username,
        password: value.password || null,
        driver: value.driver,
        encrypt: value.encrypt,
        trust_server_certificate: value.trust_server_certificate,
      }
    }
    configurationResult.value = weldingOnly ? await saveWeldingConfiguration(payload.welding) : await saveDatabaseConfiguration(payload)
    for (const source of configurationSources) {
      const value = databaseConfiguration.value[source.key]
      if (value.password) value.password_configured = true
      value.password = ''
    }
    hasUnsavedChanges.value = false
    await Promise.all([loadStatus(), loadConfiguration()])
    await loadDevices({ force: true })
  } catch (caught) {
    const exception = toError(caught)
    configurationError.value = exception.message
  } finally {
    configurationSaving.value = false
  }
}

async function runConnectionTest() {
  const welding = testIsWelding.value
  const profile = { ...activeTestConnection.value }
  testing.value = true
  testResult.value = null
  testError.value = ''
  try {
    const result = welding
      ? { ...await testWeldingConnection({ ...profile as WeldingConnection, password: profile.password || null }), database: profile.database }
      : await testDatabaseConnection(profile as SqlConnection)
    if (welding === testIsWelding.value) testResult.value = result
  } catch (caught) {
    const exception = toError(caught)
    if (welding === testIsWelding.value) testError.value = exception.message
  } finally {
    testing.value = false
  }
}

onMounted(() => Promise.all([loadStatus(), loadConfiguration()]))
</script>

<template>
  <div class="management-page database-page">
  <ManagementPageHero
    eyebrow="DATABASE ADMINISTRATION"
    title="数据库配置"
    :description="weldingScope ? '配置小组立焊接线 MySQL 只读连接；保存后新请求生效。' : '生产报表、工人身份和切割文件使用独立数据源，连接信息由服务器环境配置统一管理。'"
    icon="database"
    status-label="数据访问"
    :status-value="connectionStatus"
    :status-note="weldingScope ? '连接测试仅验证只读事务；业务表和指标另行核验' : '仅开放 SELECT / CTE，不向生产库写入'"
    tone="teal"
  />

  <section class="panel saved-database-config">
    <div class="panel-title"><div><span>01</span><h2>连接参数</h2></div><p>保存后自动热加载 · 无需重启容器</p></div>
    <div v-if="configurationLoading" class="configuration-loading">正在读取数据库配置…</div>
    <form v-else-if="databaseConfiguration" @submit.prevent="saveConfiguration(weldingScope)" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
      <div class="configuration-switches">
        <template v-if="!weldingScope">
        <label><span>生产数据模式</span><AppSelect v-model="databaseConfiguration.data_source_mode" name="data_source_mode" @change="hasUnsavedChanges = true"><option value="demo">演示数据</option><option value="sqlserver">SQL Server</option></AppSelect></label>
        <label class="check-label"><input v-model="databaseConfiguration.worker_database_auth_enabled" name="worker_database_auth_enabled" type="checkbox" /><span>启用工人 HR 库登录</span></label>
        <label class="check-label"><input v-model="databaseConfiguration.file_database_enabled" name="file_database_enabled" type="checkbox" /><span>启用切割文件库</span></label>
        </template>
        <label v-if="databaseConfiguration.welding" class="check-label"><input v-model="databaseConfiguration.welding.enabled" type="checkbox" name="weld_enabled" /><span>启用焊接产线</span></label>
      </div>
      <div class="saved-source-grid" :class="{ 'saved-source-grid--welding': weldingScope }">
        <template v-if="!weldingScope">
        <fieldset v-for="source in configurationSources" :key="source.key">
          <legend><strong>{{ source.label }}</strong><small>{{ source.description }}</small></legend>
          <label><span>服务器地址</span><input v-model.trim="databaseConfiguration[source.key].host" :name="`${source.key}_host`" autocomplete="off" spellcheck="false" placeholder="例如：10.0.0.20…" /></label>
          <label><span>端口</span><input v-model.number="databaseConfiguration[source.key].port" :name="`${source.key}_port`" type="number" inputmode="numeric" autocomplete="off" min="1" max="65535" /></label>
          <label><span>数据库名称</span><input v-model.trim="databaseConfiguration[source.key].database" :name="`${source.key}_database`" autocomplete="off" spellcheck="false" /></label>
          <label><span>只读账号</span><input v-model.trim="databaseConfiguration[source.key].username" :name="`${source.key}_username`" autocomplete="off" spellcheck="false" /></label>
          <label><span>密码</span><input v-model="databaseConfiguration[source.key].password" :name="`${source.key}_password`" type="password" autocomplete="new-password" :placeholder="databaseConfiguration[source.key].password_configured ? '已配置，留空则保留原密码…' : '请输入只读密码…'" /></label>
          <label><span>ODBC 驱动</span><input v-model.trim="databaseConfiguration[source.key].driver" :name="`${source.key}_driver`" autocomplete="off" spellcheck="false" /></label>
          <div class="saved-connection-options"><label class="check-label"><input v-model="databaseConfiguration[source.key].encrypt" :name="`${source.key}_encrypt`" type="checkbox" /><span>加密连接</span></label><label class="check-label"><input v-model="databaseConfiguration[source.key].trust_server_certificate" :name="`${source.key}_trust_certificate`" type="checkbox" /><span>信任服务器证书</span></label></div>
        </fieldset>
        </template>
      <fieldset v-if="databaseConfiguration.welding" class="welding-connection">
        <legend><strong>小组立焊接线 · MySQL</strong><small>焊接生产数据 · 独立只读连接</small></legend>
          <label><span>服务器地址</span><input v-model.trim="databaseConfiguration.welding.host" name="weld_host" autocomplete="off" :required="databaseConfiguration.welding.enabled" placeholder="请输入地址，例如 10.147.1.21" /></label>
          <label><span>实际端口</span><input v-model.number="databaseConfiguration.welding.port" name="weld_port" type="number" min="1" max="65535" /></label>
          <label><span>数据库</span><input v-model.trim="databaseConfiguration.welding.database" name="weld_database" :required="databaseConfiguration.welding.enabled" autocomplete="off" /></label>
          <label><span>只读账号</span><input v-model.trim="databaseConfiguration.welding.username" name="weld_username" :required="databaseConfiguration.welding.enabled" autocomplete="off" /></label>
          <label><span>密码</span><input v-model="databaseConfiguration.welding.password" name="weld_password" type="password" :required="databaseConfiguration.welding.enabled && !databaseConfiguration.welding.password_configured" autocomplete="new-password" :placeholder="databaseConfiguration.welding.password_configured ? '已配置，留空保留原密码' : '请输入只读密码'" /></label>
          <label><span>生产时区</span><input v-model.trim="databaseConfiguration.welding.timezone" name="weld_timezone" autocomplete="off" /></label>
      </fieldset>
      </div>
      <div class="configuration-actions">
        <span>连接测试不会保存参数；启用焊接产线后请点击“保存数据库配置”。</span>
        <button class="primary" type="submit" :disabled="configurationSaving">{{ configurationSaving ? '正在保存' : '保存数据库配置' }}</button>
      </div>
    </form>
    <div v-if="configurationResult" class="inline-result success" role="status" aria-live="polite"><strong>配置已写入</strong><span>{{ configurationResult.message }}</span></div>
    <div v-if="configurationError" class="inline-result error" role="alert"><strong>配置操作失败</strong><span>{{ configurationError }}，请检查连接参数后重试。</span></div>
  </section>

  <section class="panel database-sources-panel">
    <div class="panel-title"><div><span>02</span><h2>业务数据源</h2></div><p>状态卡不显示账号和密码</p></div>
    <div class="database-source-list" :class="{ 'database-source-list--welding': weldingScope }">
      <article v-for="source in dataSources" :key="source.source">
        <div><strong>{{ source.label }}</strong><span :class="source.enabled && source.configured ? 'ready' : 'muted'">{{ !source.enabled ? '未启用' : source.configured ? '已配置' : '配置不完整' }}</span></div>
        <dl><div><dt>服务器</dt><dd>{{ source.host ? `${source.host}:${source.port}` : '--' }}</dd></div><div><dt>数据库</dt><dd>{{ source.database || '--' }}</dd></div></dl>
        <small v-if="source.uses_primary_fallback" class="source-warning">当前兼容回退到生产库；正式环境请配置专用连接。</small>
        <small v-else-if="source.configured">已使用独立连接配置</small>
        <small v-else>连接参数尚未保存完整</small>
      </article>
    </div>
  </section>

  <div v-if="statusError" class="global-error" role="alert"><strong>状态读取失败</strong><span>{{ statusError }}，请重新检查数据库状态。</span></div>

  <section v-if="!weldingScope" class="db-status-grid" aria-label="数据库状态">
    <article class="db-status-card">
      <span>当前数据源</span>
      <strong>{{ sourceName }}</strong>
      <small>{{ sourceDescription }}</small>
    </article>
    <article class="db-status-card">
      <span>ODBC 驱动</span>
      <strong :class="status?.pyodbc_installed ? 'ok-text' : 'warn-text'">{{ status?.pyodbc_installed ? '已安装' : '未安装' }}</strong>
      <small>{{ status?.driver || '正在检查' }}</small>
    </article>
    <article class="db-status-card">
      <span>连接配置</span>
      <strong :class="status?.connection_configured ? 'ok-text' : 'warn-text'">{{ status?.connection_configured ? '已配置' : '待配置' }}</strong>
      <small>{{ status?.configured_database || '未填写数据库名称' }}</small>
    </article>
    <article class="db-status-card">
      <span>SQL 映射</span>
      <strong :class="status?.all_queries_ready ? 'ok-text' : 'warn-text'">{{ status?.all_queries_ready ? '全部就绪' : '存在占位模板' }}</strong>
      <small>车间 / 汇总 / 设备</small>
    </article>
  </section>

  <section class="panel db-panel">
    <div class="panel-title"><div><span>03</span><h2>连接测试</h2></div><p>凭据仅用于本次测试，不会保存</p></div>
    <div v-if="!weldingScope" class="connection-test-source"><label><span>数据库类型</span><AppSelect v-model="testSource" :disabled="testing" name="test_source"><option value="sqlserver">SQL Server</option><option value="welding">小组立焊接线 · MySQL</option></AppSelect></label></div>
    <form class="connection-form" @submit.prevent="runConnectionTest">
      <label><span>服务器地址</span><input v-model="activeTestConnection.host" name="test_host" required placeholder="例如：192.168.1.20…" autocomplete="off" spellcheck="false" /></label>
      <label><span>端口</span><input v-model.number="activeTestConnection.port" name="test_port" type="number" inputmode="numeric" autocomplete="off" min="1" max="65535" required /></label>
      <label><span>数据库名称</span><input v-model="activeTestConnection.database" name="test_database" required autocomplete="off" spellcheck="false" /></label>
      <label><span>只读账号</span><input v-model="activeTestConnection.username" name="test_username" required autocomplete="username" spellcheck="false" /></label>
      <label><span>密码</span><input v-model="activeTestConnection.password" name="test_password" type="password" :required="!testIsWelding || !databaseConfiguration?.welding?.password_configured" :placeholder="testIsWelding && databaseConfiguration?.welding?.password_configured ? '已配置，留空使用原密码' : ''" autocomplete="current-password" /></label>
      <label v-if="testIsWelding" class="driver-field"><span>生产时区</span><input v-model.trim="weldingConnection.timezone" name="test_timezone" required autocomplete="off" spellcheck="false" /></label>
      <label v-else class="driver-field"><span>ODBC 驱动</span><input v-model="connection.driver" name="test_driver" required autocomplete="off" spellcheck="false" /></label>
      <div v-if="!testIsWelding" class="connection-options">
        <label class="check-label"><input v-model="connection.encrypt" name="test_encrypt" type="checkbox" /><span>加密连接</span></label>
        <label class="check-label"><input v-model="connection.trust_server_certificate" name="test_trust_certificate" type="checkbox" /><span>信任服务器证书</span></label>
      </div>
      <button class="primary test-button" type="submit" :disabled="testing">
        <span v-if="testing" class="spinner"></span>{{ testing ? '正在测试' : '测试连接' }}
      </button>
    </form>
    <div v-if="testResult" class="inline-result success" role="status" aria-live="polite"><strong>连接成功</strong><span>{{ testResult.database }} · {{ formatCell(testResult.elapsed_ms) }} ms · 凭据未保存</span></div>
    <p v-if="testResult?.message" class="connection-test-note">{{ testResult.message }}</p>
    <div v-if="testError" class="inline-result error" role="alert"><strong>连接失败</strong><span>{{ testError }}，请核对地址、账号和网络后重试。</span></div>
  </section>

  </div>
</template>

<style scoped>
.database-page > .db-status-grid,
.database-page > .global-error { margin-bottom:0; }
.configuration-loading { padding:34px 24px; color:var(--muted); }
.configuration-switches { display:flex; flex-wrap:wrap; align-items:flex-end; gap:22px; padding:20px 24px; border-bottom:1px solid var(--line); }
.configuration-switches > label:first-child { min-width:190px; }.configuration-switches label > span,.saved-source-grid label > span { display:block; margin-bottom:6px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.configuration-switches .check-label,.saved-connection-options .check-label { display:flex; align-items:center; gap:7px; }.configuration-switches .check-label span,.saved-connection-options .check-label span { margin:0; }
.saved-source-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; padding:20px 24px; }
.saved-source-grid fieldset { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) 92px; gap:11px; margin:0; padding:16px; border:1px solid var(--theme-border-color); }
.saved-source-grid legend { width:100%; display:flex; flex-direction:column; gap:3px; padding:0 7px; color:var(--navy); }.saved-source-grid legend strong { font-size:13px; }.saved-source-grid legend small { color:var(--muted); font-size:var(--font-auxiliary); font-weight:400; }
.saved-source-grid fieldset > label:nth-of-type(3),.saved-source-grid fieldset > label:nth-of-type(4),.saved-source-grid fieldset > label:nth-of-type(5),.saved-source-grid fieldset > label:nth-of-type(6),.saved-connection-options { grid-column:1/-1; }
.saved-connection-options { display:flex; flex-wrap:wrap; gap:12px; padding-top:3px; }
.saved-source-grid--welding { grid-template-columns:1fr; }
.configuration-actions { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:0 24px 24px; }.configuration-actions > span { color:var(--muted); font-size:var(--font-auxiliary); }
.database-source-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; padding:20px 24px 24px; }
.database-source-list--welding { grid-template-columns:1fr; }
.connection-test-source { padding:20px 24px 0; }
.connection-test-source label { display:block; max-width:320px; }
.connection-test-source label > span { display:block; margin-bottom:6px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.connection-test-note { margin:0 24px 16px; color:var(--text-color-3); font-size:var(--font-auxiliary); line-height:1.6; }
.database-source-list article { min-width:0; padding:16px; border:1px solid var(--theme-border-color); background:rgb(var(--info-color) / .1); }
.database-source-list article > div { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.database-source-list strong { color:var(--navy); font-size:13px; }
.database-source-list article > div > span { padding:3px 7px; border-radius:999px; font-size:var(--font-auxiliary); white-space:nowrap; }
.database-source-list .ready { color:rgb(var(--success-color)); background:rgb(var(--success-color) / .1); }
.database-source-list .muted { color:var(--text-color-2); background:rgb(var(--warning-color) / .1); }
.database-source-list dl { margin:14px 0 10px; }
.database-source-list dl div { display:grid; grid-template-columns:58px minmax(0,1fr); gap:8px; padding:5px 0; border-top:1px solid var(--theme-border-color); font-size:var(--font-auxiliary); }
.database-source-list dt { color:var(--muted); }.database-source-list dd { overflow:hidden; margin:0; color:var(--text-color-2); text-overflow:ellipsis; white-space:nowrap; }
.database-source-list small { color:var(--text-color-3); line-height:1.5; }.database-source-list .source-warning { color:rgb(var(--warning-color)); }
@media (max-width:1100px) { .saved-source-grid { grid-template-columns:1fr; } }
@media (max-width:900px) { .database-source-list { grid-template-columns:1fr; }.configuration-switches,.configuration-actions { align-items:stretch; flex-direction:column; } }
</style>
