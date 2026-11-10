<script setup lang="ts">
import type { ConnectionTestResult, DatabaseConfiguration, DatabaseSource, DatabaseStatus, SaveResult, SqlConnection, WeldingConnection } from '../types/configuration'

import { toError } from '../utils/errors'
import { selectedLineType } from '../utils/productionLines.ts'
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { getDatabaseConfiguration, getDatabaseStatus, saveDatabaseConfiguration, testDatabaseConnection, testWeldingConnection, saveWeldingConfiguration } from '../api/configuration.ts'
import { deviceSelection, loadDevices } from '../state/devices.ts'
import { formatTableDateValue } from '../utils/dateDisplay.ts'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import AppReportTable from '../components/AppReportTable.vue'
import { NButton, NCheckbox, NInput, NInputNumber, NModal, NTag } from 'naive-ui'
import DatabaseSourcesPanel from '../components/DatabaseSourcesPanel.vue'
import { registerDeviceSwitchGuard } from '../api/deviceContext.ts'
import { useConfirm } from '../composables/useConfirm.ts'
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
const weldingScope = computed(() => selectedLineType(deviceSelection.items, deviceSelection.id) === 'welding')
const testSource = ref('sqlserver')
const testIsWelding = computed(() => weldingScope.value || testSource.value === 'welding')
const dataSources = computed(() => (status.value?.data_sources || []).filter(source => !weldingScope.value || source.source === deviceSelection.items.find(line => line.id === deviceSelection.id)?.source_key))
const connectionStatus = computed(() => weldingScope.value
  ? databaseConfiguration.value?.welding?.password_configured ? '焊接参数已配置' : '等待配置'
  : status.value?.connection_configured ? '只读连接已配置' : '等待配置')
const hasUnsavedChanges = ref(false)
type ConnectionEditor = { key: DatabaseSource; profile: SqlConnection } | { key: 'welding'; profile: WeldingConnection }
const editor = ref<ConnectionEditor | null>(null)
const editorDirty = ref(false)
useUnsavedChanges(computed(() => hasUnsavedChanges.value || editorDirty.value), '数据库连接参数还有未保存的修改，确定离开吗？')
const confirm = useConfirm()
onBeforeUnmount(registerDeviceSwitchGuard('database', () => !configurationSaving.value && (!(hasUnsavedChanges.value || editorDirty.value) || confirm('数据库连接参数还有未保存的修改，确定切换产线吗？'))))
const configurationSources: { key: DatabaseSource; label: string; description: string }[] = [
  { key: 'production', label: '生产报表库', description: '当前切割产线绑定的数据源 · dbo.task 等生产数据' },
  { key: 'worker', label: '工人 HR 库', description: 'WORKER_SOURCE_KEY · dbo.HRbase_userInfo' },
  { key: 'file', label: '切割文件库', description: 'FILE_SOURCE_KEY · PDM 文件索引/内容' },
]
const savedConnections = computed(() => {
  const configuration = databaseConfiguration.value
  if (!configuration) return []
  return [
    ...(!weldingScope.value ? configurationSources.map(source => ({ ...source, engine: 'SQL Server', profile: configuration[source.key] })) : []),
    ...(configuration.welding ? [{ key: 'welding' as const, label: '小组立焊接线', description: '焊接生产数据 · 独立只读连接', engine: 'MySQL', profile: configuration.welding }] : []),
  ]
})
const connectionColumns = [
  { key: 'c1', title: '业务用途', align: 'left' },
  { key: 'c2', title: '数据库类型', align: 'left' },
  { key: 'c3', title: '服务器', align: 'left' },
  { key: 'c4', title: '数据库', align: 'left' },
  { key: 'c5', title: '只读账号', align: 'left' },
  { key: 'c6', title: '密码', align: 'center' },
  { key: 'c7', title: '操作', align: 'center', ellipsis: false },
] as const
const editorTitle = computed(() => `编辑${savedConnections.value.find(source => source.key === editor.value?.key)?.label || '连接参数'}`)

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
watch(() => deviceSelection.id, () => { editor.value = null; editorDirty.value = false; configurationResult.value = null; testResult.value = null; testError.value = ''; void loadConfiguration(); void loadStatus() })
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
  const line = deviceSelection.id
  statusError.value = ''
  try {
    const result = await getDatabaseStatus()
    if (line !== deviceSelection.id) return
    status.value = result
    connection.driver = status.value.driver
    connection.host = status.value.configured_host || ''
    connection.database = status.value.configured_database || ''
  } catch (caught) {
    const exception = toError(caught)
    if (line === deviceSelection.id) statusError.value = exception.message
  }
}

async function loadConfiguration() {
  const line = deviceSelection.id
  configurationLoading.value = true
  configurationError.value = ''
  try {
    const result = await getDatabaseConfiguration()
    if (line !== deviceSelection.id) return
    databaseConfiguration.value = result
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
    if (line === deviceSelection.id) configurationError.value = exception.message
  } finally {
    if (line === deviceSelection.id) configurationLoading.value = false
  }
}

function editConnection(key: DatabaseSource | 'welding') {
  const configuration = databaseConfiguration.value
  if (!configuration || configurationSaving.value) return
  if (key === 'welding') {
    if (!configuration.welding) return
    editor.value = { key, profile: { ...configuration.welding, password: '' } }
  } else editor.value = { key, profile: { ...configuration[key], password: '' } }
  editorDirty.value = false
  configurationError.value = ''
}

async function closeEditor() {
  if (configurationSaving.value || (editorDirty.value && !await confirm('连接参数尚未保存，确定关闭吗？'))) return
  editor.value = null
  editorDirty.value = false
}

async function saveEditor() {
  if (!editor.value || configurationSaving.value) return
  if (await saveConfiguration(editor.value.key === 'welding', editor.value)) {
    editor.value = null
    editorDirty.value = false
  }
}

async function saveConfiguration(weldingOnly = false, edited: ConnectionEditor | null = null) {
  const line = deviceSelection.id
  configurationSaving.value = true
  configurationError.value = ''
  configurationResult.value = null
  try {
    if (!databaseConfiguration.value) return false
    const weldingProfile = edited?.key === 'welding' ? edited.profile : databaseConfiguration.value.welding
    const payload: DatabaseConfiguration = {
      production: databaseConfiguration.value.production, worker: databaseConfiguration.value.worker, file: databaseConfiguration.value.file,
      data_source_mode: databaseConfiguration.value.data_source_mode,
      worker_database_auth_enabled: databaseConfiguration.value.worker_database_auth_enabled,
      file_database_enabled: databaseConfiguration.value.file_database_enabled,
      welding: weldingProfile ? { ...weldingProfile, password: weldingProfile.password || null } : null,
    }
    for (const source of configurationSources) {
      const value = edited && edited.key === source.key ? edited.profile : databaseConfiguration.value[source.key]
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
    const result = weldingOnly && payload.welding ? await saveWeldingConfiguration(payload.welding) : await saveDatabaseConfiguration(payload)
    if (line !== deviceSelection.id) return false
    configurationResult.value = result
    for (const source of configurationSources) {
      const value = databaseConfiguration.value[source.key]
      if (value.password) value.password_configured = true
      value.password = ''
    }
    hasUnsavedChanges.value = false
    await Promise.all([loadStatus(), loadConfiguration()])
    await loadDevices({ force: true })
    return true
  } catch (caught) {
    const exception = toError(caught)
    if (line === deviceSelection.id) configurationError.value = exception.message
    return false
  } finally {
    configurationSaving.value = false
  }
}

async function runConnectionTest() {
  const line = deviceSelection.id
  const welding = testIsWelding.value
  const weldingProfile = { ...weldingConnection }
  const sqlProfile = { ...connection }
  testing.value = true
  testResult.value = null
  testError.value = ''
  try {
    const result = welding
      ? { ...await testWeldingConnection({ ...weldingProfile, password: weldingProfile.password || null }), database: weldingProfile.database }
      : await testDatabaseConnection(sqlProfile)
    if (line === deviceSelection.id && welding === testIsWelding.value) testResult.value = result
  } catch (caught) {
    const exception = toError(caught)
    if (line === deviceSelection.id && welding === testIsWelding.value) testError.value = exception.message
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
    :description="weldingScope ? '配置当前焊接产线绑定的 MySQL 只读连接；保存后新请求生效。' : '生产报表、工人身份和切割文件使用独立数据源，连接信息由服务器环境配置统一管理。'"
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
        <NCheckbox v-model:checked="databaseConfiguration.worker_database_auth_enabled" name="worker_database_auth_enabled" @update:checked="hasUnsavedChanges = true" class="check-label"><span>启用工人 HR 库登录</span></NCheckbox>
        <NCheckbox v-model:checked="databaseConfiguration.file_database_enabled" name="file_database_enabled" @update:checked="hasUnsavedChanges = true" class="check-label"><span>启用切割文件库</span></NCheckbox>
        </template>
        <NCheckbox v-model:checked="databaseConfiguration.welding.enabled" name="weld_enabled" @update:checked="hasUnsavedChanges = true" v-if="databaseConfiguration.welding" class="check-label"><span>启用焊接产线</span></NCheckbox>
      </div>
      <div class="saved-connection-table">
        <AppReportTable :items="savedConnections" :columns="connectionColumns" :row-key="row => row.key" :paginate="false" aria-label="业务连接参数">
          <template #c1="{ row }"><strong :title="row.description">{{ row.label }}</strong></template>
          <template #c2="{ row }">{{ row.engine }}</template>
          <template #c3="{ row }">{{ row.profile.host ? `${row.profile.host}:${row.profile.port}` : '--' }}</template>
          <template #c4="{ row }">{{ row.profile.database || '--' }}</template>
          <template #c5="{ row }">{{ row.profile.username || '--' }}</template>
          <template #c6="{ row }"><NTag size="small" :type="row.profile.password_configured ? 'success' : 'default'">{{ row.profile.password_configured ? '已配置' : '未配置' }}</NTag></template>
          <template #c7="{ row }"><NButton attr-type="button" size="small" :disabled="configurationSaving" @click="editConnection(row.key)">编辑</NButton></template>
        </AppReportTable>
      </div>
      <div class="configuration-actions">
        <span>连接参数通过表格中的“编辑”修改；模式和启用状态修改后请保存。</span>
        <NButton class="primary" attr-type="submit" :disabled="configurationSaving" type="primary" :loading="configurationSaving">{{ configurationSaving ? '正在保存' : '保存数据库配置' }}</NButton>
      </div>
    </form>
    <div v-if="configurationResult" class="inline-result success" role="status" aria-live="polite"><strong>配置已写入</strong><span>{{ configurationResult.message }}</span></div>
    <div v-if="configurationError" class="inline-result error" role="alert"><strong>配置操作失败</strong><span>{{ configurationError }}，请检查连接参数后重试。</span></div>
  </section>

  <NModal :show="Boolean(editor)" preset="card" :title="editorTitle" class="connection-editor-modal" :style="{ width: 'min(720px, calc(100vw - 32px))' }" :mask-closable="false" :closable="!configurationSaving" :close-on-esc="!configurationSaving" @update:show="shown => { if (!shown) closeEditor() }">
    <form v-if="editor" @submit.prevent="saveEditor" @input.capture="editorDirty = true" @change.capture="editorDirty = true">
      <fieldset v-if="editor.key !== 'welding'" class="connection-editor-fields">
          <label><span>服务器地址</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.host" :name="`${editor.key}_host`" autocomplete="off" spellcheck="false" placeholder="例如：10.0.0.20…" :input-props="{ 'name': `${editor.key}_host`, 'autocomplete': 'off', 'spellcheck': false }" /></label>
          <label><span>端口</span><NInputNumber :disabled="configurationSaving" v-model:value="editor.profile.port" :name="`${editor.key}_port`" inputmode="numeric" autocomplete="off" :min="1" :max="65535" :input-props="{ 'name': `${editor.key}_port`, 'inputmode': 'numeric', 'autocomplete': 'off' }" @update:value="editorDirty = true" :precision="0" /></label>
          <label><span>数据库名称</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.database" :name="`${editor.key}_database`" autocomplete="off" spellcheck="false" :input-props="{ 'name': `${editor.key}_database`, 'autocomplete': 'off', 'spellcheck': false }" /></label>
          <label><span>只读账号</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.username" :name="`${editor.key}_username`" autocomplete="off" spellcheck="false" :input-props="{ 'name': `${editor.key}_username`, 'autocomplete': 'off', 'spellcheck': false }" /></label>
          <label><span>密码</span><NInput :disabled="configurationSaving" v-model:value="editor.profile.password" :name="`${editor.key}_password`" type="password" show-password-on="click" autocomplete="new-password" :placeholder="editor.profile.password_configured ? '已配置，留空则保留原密码…' : '请输入只读密码…'" :input-props="{ 'name': `${editor.key}_password`, 'autocomplete': 'new-password' }" /></label>
          <label><span>ODBC 驱动</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.driver" :name="`${editor.key}_driver`" autocomplete="off" spellcheck="false" :input-props="{ 'name': `${editor.key}_driver`, 'autocomplete': 'off', 'spellcheck': false }" /></label>
          <div class="saved-connection-options"><NCheckbox :disabled="configurationSaving" v-model:checked="editor.profile.encrypt" :name="`${editor.key}_encrypt`" @update:checked="editorDirty = true" class="check-label"><span>加密连接</span></NCheckbox><NCheckbox :disabled="configurationSaving" v-model:checked="editor.profile.trust_server_certificate" :name="`${editor.key}_trust_certificate`" @update:checked="editorDirty = true" class="check-label"><span>信任服务器证书</span></NCheckbox></div>
      </fieldset>
      <fieldset v-else class="connection-editor-fields">
          <label><span>服务器地址</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.host" name="weld_host" autocomplete="off" :required="editor.profile.enabled" placeholder="请输入地址，例如 10.147.1.21" :input-props="{ 'name': 'weld_host', 'autocomplete': 'off', 'required': editor.profile.enabled }" /></label>
          <label><span>实际端口</span><NInputNumber :disabled="configurationSaving" v-model:value="editor.profile.port" name="weld_port" :min="1" :max="65535" :input-props="{ 'name': 'weld_port' }" @update:value="editorDirty = true" :precision="0" /></label>
          <label><span>数据库</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.database" name="weld_database" :required="editor.profile.enabled" autocomplete="off" :input-props="{ 'name': 'weld_database', 'required': editor.profile.enabled, 'autocomplete': 'off' }" /></label>
          <label><span>只读账号</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.username" name="weld_username" :required="editor.profile.enabled" autocomplete="off" :input-props="{ 'name': 'weld_username', 'required': editor.profile.enabled, 'autocomplete': 'off' }" /></label>
          <label><span>密码</span><NInput :disabled="configurationSaving" v-model:value="editor.profile.password" name="weld_password" type="password" show-password-on="click" :required="editor.profile.enabled && !editor.profile.password_configured" autocomplete="new-password" :placeholder="editor.profile.password_configured ? '已配置，留空保留原密码' : '请输入只读密码'" :input-props="{ 'name': 'weld_password', 'required': editor.profile.enabled && !editor.profile.password_configured, 'autocomplete': 'new-password' }" /></label>
          <label><span>生产时区</span><NInput :disabled="configurationSaving" v-model:value.trim="editor.profile.timezone" name="weld_timezone" autocomplete="off" :input-props="{ 'name': 'weld_timezone', 'autocomplete': 'off' }" /></label>
      </fieldset>
      <p v-if="configurationError" class="connection-editor-error" role="alert">{{ configurationError }}</p>
      <div class="connection-editor-actions"><NButton :disabled="configurationSaving" @click="closeEditor">取消</NButton><NButton attr-type="submit" type="primary" :disabled="configurationSaving" :loading="configurationSaving">保存数据库配置</NButton></div>
    </form>
  </NModal>

  <section class="panel database-sources-panel">
    <div class="panel-title"><div><span>02</span><h2>业务数据源</h2></div><p>状态卡不显示账号和密码</p></div>
    <div class="database-source-list" :class="{ 'database-source-list--welding': weldingScope }">
      <article v-for="source in dataSources" :key="source.source">
        <div><strong>{{ source.label }}</strong><span :class="source.enabled && source.configured ? 'ready' : 'muted'">{{ !source.enabled ? '未启用' : source.configured ? '已配置' : '配置不完整' }}</span></div>
        <dl><div><dt>服务器</dt><dd>{{ source.host ? `${source.host}:${source.port}` : '--' }}</dd></div><div><dt>数据库</dt><dd>{{ source.database || '--' }}</dd></div></dl>
        <small v-if="source.configured">已登记数据源连接</small>
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

  <DatabaseSourcesPanel />

  <section class="panel db-panel">
    <div class="panel-title"><div><span>03</span><h2>连接测试</h2></div><p>凭据仅用于本次测试，不会保存</p></div>
    <div v-if="!weldingScope" class="connection-test-source"><label><span>数据库类型</span><AppSelect v-model="testSource" :disabled="testing" name="test_source"><option value="sqlserver">SQL Server</option><option value="welding">小组立焊接线 · MySQL</option></AppSelect></label></div>
    <form class="connection-form" @submit.prevent="runConnectionTest">
      <label><span>服务器地址</span><NInput v-model:value="activeTestConnection.host" name="test_host" required placeholder="例如：192.168.1.20…" autocomplete="off" spellcheck="false" :input-props="{ 'name': 'test_host', 'required': true, 'autocomplete': 'off', 'spellcheck': false }" /></label>
      <label><span>端口</span><NInputNumber v-model:value="activeTestConnection.port" name="test_port" inputmode="numeric" autocomplete="off" :min="1" :max="65535" required :input-props="{ 'name': 'test_port', 'inputmode': 'numeric', 'autocomplete': 'off', 'required': true }" :precision="0" /></label>
      <label><span>数据库名称</span><NInput v-model:value="activeTestConnection.database" name="test_database" required autocomplete="off" spellcheck="false" :input-props="{ 'name': 'test_database', 'required': true, 'autocomplete': 'off', 'spellcheck': false }" /></label>
      <label><span>只读账号</span><NInput v-model:value="activeTestConnection.username" name="test_username" required autocomplete="username" spellcheck="false" :input-props="{ 'name': 'test_username', 'required': true, 'autocomplete': 'username', 'spellcheck': false }" /></label>
      <label><span>密码</span><NInput v-model:value="activeTestConnection.password" name="test_password" type="password" show-password-on="click" :required="!testIsWelding || !databaseConfiguration?.welding?.password_configured" :placeholder="testIsWelding && databaseConfiguration?.welding?.password_configured ? '已配置，留空使用原密码' : ''" autocomplete="current-password" :input-props="{ 'name': 'test_password', 'required': !testIsWelding || !databaseConfiguration?.welding?.password_configured, 'autocomplete': 'current-password' }" /></label>
      <label v-if="testIsWelding" class="driver-field"><span>生产时区</span><NInput v-model:value.trim="weldingConnection.timezone" name="test_timezone" required autocomplete="off" spellcheck="false" :input-props="{ 'name': 'test_timezone', 'required': true, 'autocomplete': 'off', 'spellcheck': false }" /></label>
      <label v-else class="driver-field"><span>ODBC 驱动</span><NInput v-model:value="connection.driver" name="test_driver" required autocomplete="off" spellcheck="false" :input-props="{ 'name': 'test_driver', 'required': true, 'autocomplete': 'off', 'spellcheck': false }" /></label>
      <div v-if="!testIsWelding" class="connection-options">
        <NCheckbox v-model:checked="connection.encrypt" name="test_encrypt"  class="check-label"><span>加密连接</span></NCheckbox>
        <NCheckbox v-model:checked="connection.trust_server_certificate" name="test_trust_certificate"  class="check-label"><span>信任服务器证书</span></NCheckbox>
      </div>
      <NButton class="primary test-button" attr-type="submit" :disabled="testing" type="primary" :loading="testing">
        {{ testing ? '正在测试' : '测试连接' }}
      </NButton>
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
.configuration-switches > label:first-child { min-width:190px; }.configuration-switches label > span,.connection-editor-fields label > span { display:block; margin-bottom:6px; color:var(--text-color-2); font-size:var(--font-auxiliary); font-weight:700; }
.configuration-switches .check-label,.saved-connection-options .check-label { display:flex; align-items:center; gap:7px; }.configuration-switches .check-label span,.saved-connection-options .check-label span { margin:0; }
.saved-connection-table { padding:16px 24px; }
.connection-editor-fields { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) 100px; gap:16px; margin:0; padding:0; border:0; }
.connection-editor-fields > label { min-width:0; }
.connection-editor-fields > label:nth-of-type(n+3),.saved-connection-options { grid-column:1/-1; }
.saved-connection-options { display:flex; flex-wrap:wrap; gap:12px; padding-top:4px; }
.connection-editor-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:24px; }
.connection-editor-error { color:rgb(var(--error-color)); font-size:var(--font-auxiliary); }
@media(max-width:560px) { .saved-connection-table { padding:16px; }.connection-editor-fields { grid-template-columns:minmax(0,1fr); } }
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
@media (max-width:900px) { .database-source-list { grid-template-columns:1fr; }.configuration-switches,.configuration-actions { align-items:stretch; flex-direction:column; } }
</style>
