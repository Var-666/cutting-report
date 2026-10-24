<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  executeWorkbenchSql,
  getDatabaseConfiguration,
  getDatabaseStatus,
  previewDatabaseDataset,
  saveDatabaseConfiguration,
  testDatabaseConnection,
  validateWorkbenchSql,
} from '../api/reports'
import { formatTableDateValue } from '../utils/dateDisplay.js'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.js'

const status = ref(null)
const loadingStatus = ref(false)
const statusError = ref('')
const databaseConfiguration = ref(null)
const configurationLoading = ref(false)
const configurationSaving = ref(false)
const configurationError = ref('')
const configurationResult = ref(null)
const testing = ref(false)
const testResult = ref(null)
const testError = ref('')
const previewing = ref(false)
const previewResult = ref(null)
const previewError = ref('')
const validatingSql = ref(false)
const executingSql = ref(false)
const sqlError = ref('')
const sqlValidation = ref(null)
const sqlResult = ref(null)
const hasUnsavedChanges = ref(false)
useUnsavedChanges(hasUnsavedChanges, '数据库连接参数还有未保存的修改，确定离开吗？')
const configurationSources = [
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

const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
const preview = reactive({
  dataset: 'workshop',
  report_date: today,
  project_code: '',
  ship_no: '',
  limit: 50,
})

const workbench = reactive({
  token: '',
  maxRows: 100,
  sql: `SELECT TOP (100)
    TASK_ID,
    MACHINE_NO,
    STATUS,
    OP_DATE,
    OP_LENGTH_CUT,
    OP_LENGTH_GROOVE,
    WORK_TIME
FROM [dbo].[task]
ORDER BY TASK_ID DESC`,
})

const previewColumns = computed(() => {
  const first = previewResult.value?.items?.[0]
  return first ? Object.keys(first) : []
})
const sqlResultColumns = computed(() => sqlResult.value?.columns || [])
const sourceName = computed(() => ({ demo: '演示数据', sqlserver: 'SQL Server' }[status.value?.mode] || '正在检查') )
const sourceDescription = computed(() => {
  if (status.value?.mode === 'sqlserver') return '生产查询模式'
  return '尚未连接生产库'
})
const modeLabel = (mode) => ({ demo: '演示数据', sqlserver: 'SQL Server' }[mode] || '未知数据源')

const fieldLabels = {
  task_id: '任务ID', project_code: '工程号', ship_no: '船号', segment_name: '分段',
  program_no: '程序号', equipment_id: '设备号', cut_length: '切割长度',
  groove_length: '坡口长度', work_time: '切割时长', gas_consumption: '气耗',
  electricity_consumption: '电耗', production_time: '生产时间',
  source_updated_at: '数据更新时间', remark: '备注',
}

function labelFor(key) {
  return fieldLabels[key] || key
}

function formatCell(value) {
  if (value === null || value === undefined || value === '') return '--'
  if (typeof value === 'number') return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 1 }).format(value)
  return formatTableDateValue(value)
}

async function loadStatus() {
  loadingStatus.value = true
  statusError.value = ''
  try {
    status.value = await getDatabaseStatus()
    connection.driver = status.value.driver
    connection.host = status.value.configured_host || ''
    connection.database = status.value.configured_database || ''
  } catch (exception) {
    statusError.value = exception.message
  } finally {
    loadingStatus.value = false
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
    hasUnsavedChanges.value = false
  } catch (exception) {
    configurationError.value = exception.message
  } finally {
    configurationLoading.value = false
  }
}

async function saveConfiguration() {
  configurationSaving.value = true
  configurationError.value = ''
  configurationResult.value = null
  try {
    const payload = {
      data_source_mode: databaseConfiguration.value.data_source_mode,
      worker_database_auth_enabled: databaseConfiguration.value.worker_database_auth_enabled,
      file_database_enabled: databaseConfiguration.value.file_database_enabled,
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
    configurationResult.value = await saveDatabaseConfiguration(payload)
    for (const source of configurationSources) {
      const value = databaseConfiguration.value[source.key]
      if (value.password) value.password_configured = true
      value.password = ''
    }
    hasUnsavedChanges.value = false
  } catch (exception) {
    configurationError.value = exception.message
  } finally {
    configurationSaving.value = false
  }
}

async function runConnectionTest() {
  testing.value = true
  testResult.value = null
  testError.value = ''
  try {
    testResult.value = await testDatabaseConnection({ ...connection })
  } catch (exception) {
    testError.value = exception.message
  } finally {
    testing.value = false
  }
}

async function runPreview() {
  previewing.value = true
  previewError.value = ''
  try {
    previewResult.value = await previewDatabaseDataset({
      ...preview,
      project_code: preview.project_code || null,
      ship_no: preview.ship_no || null,
    })
  } catch (exception) {
    previewError.value = exception.message
    previewResult.value = null
  } finally {
    previewing.value = false
  }
}

async function validateSql() {
  validatingSql.value = true
  sqlError.value = ''
  sqlValidation.value = null
  try {
    sqlValidation.value = await validateWorkbenchSql(workbench.sql)
    workbench.sql = sqlValidation.value.formatted_sql
  } catch (exception) {
    sqlError.value = exception.message
  } finally {
    validatingSql.value = false
  }
}

async function executeSql() {
  executingSql.value = true
  sqlError.value = ''
  sqlResult.value = null
  try {
    sqlResult.value = await executeWorkbenchSql({
      sql: workbench.sql,
      maxRows: workbench.maxRows,
      token: workbench.token,
    })
  } catch (exception) {
    sqlError.value = exception.message
  } finally {
    executingSql.value = false
  }
}

function setSqlExample(type) {
  const examples = {
    select: `SELECT TOP (100)\n    TASK_ID, MACHINE_NO, STATUS, OP_DATE, OP_LENGTH_CUT, WORK_TIME\nFROM [dbo].[task]\nORDER BY TASK_ID DESC`,
    aggregate: `SELECT\n    OP_DATE,\n    COUNT(*) AS task_count,\n    SUM(OP_LENGTH_CUT) / 1000.0 AS total_cut_length_m,\n    SUM(WORK_TIME) AS total_work_time_seconds\nFROM [dbo].[task]\nWHERE STATUS = 2 AND WORK_TIME >= 0\nGROUP BY OP_DATE\nORDER BY OP_DATE DESC`,
    cte: `WITH completed_tasks AS (\n    SELECT\n        TRY_CONVERT(date, OP_DATE, 23) AS production_date,\n        OP_LENGTH_CUT,\n        WORK_TIME\n    FROM [dbo].[task]\n    WHERE STATUS = 2 AND WORK_TIME >= 0\n)\nSELECT\n    production_date,\n    SUM(OP_LENGTH_CUT) / 1000.0 AS cut_length_m,\n    SUM(WORK_TIME) AS work_time_seconds\nFROM completed_tasks\nGROUP BY production_date\nORDER BY production_date DESC`,
  }
  workbench.sql = examples[type]
  sqlValidation.value = null
  sqlResult.value = null
  sqlError.value = ''
}

onMounted(() => Promise.all([loadStatus(), loadConfiguration()]))
</script>

<template>
  <div class="management-page database-page">
  <ManagementPageHero
    eyebrow="DATABASE ADMINISTRATION"
    title="数据库配置"
    description="生产报表、工人身份和切割文件使用独立数据源，连接信息由服务器环境配置统一管理。"
    icon="database"
    status-label="数据访问"
    :status-value="status?.connection_configured ? '只读连接已配置' : '等待配置'"
    status-note="仅开放 SELECT / CTE，不向生产库写入"
    tone="teal"
  />

  <section class="panel saved-database-config">
    <div class="panel-title"><div><span>01</span><h2>连接参数</h2></div><p>保存到 data/settings.env · 重启服务后生效</p></div>
    <div v-if="configurationLoading" class="configuration-loading">正在读取数据库配置…</div>
    <form v-else-if="databaseConfiguration" @submit.prevent="saveConfiguration" @input.capture="hasUnsavedChanges = true" @change.capture="hasUnsavedChanges = true">
      <div class="configuration-switches">
        <label><span>生产数据模式</span><AppSelect v-model="databaseConfiguration.data_source_mode" name="data_source_mode" @change="hasUnsavedChanges = true"><option value="demo">演示数据</option><option value="sqlserver">SQL Server</option></AppSelect></label>
        <label class="check-label"><input v-model="databaseConfiguration.worker_database_auth_enabled" name="worker_database_auth_enabled" type="checkbox" /><span>启用工人 HR 库登录</span></label>
        <label class="check-label"><input v-model="databaseConfiguration.file_database_enabled" name="file_database_enabled" type="checkbox" /><span>启用切割文件库</span></label>
      </div>
      <div class="saved-source-grid">
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
      </div>
      <div class="configuration-actions">
        <span>报表设计和自动化配置继续保存在系统 SQLite 中。</span>
        <button class="primary" type="submit" :disabled="configurationSaving">{{ configurationSaving ? '正在保存' : '保存数据库配置' }}</button>
      </div>
    </form>
    <div v-if="configurationResult" class="inline-result success" role="status" aria-live="polite"><strong>配置已写入</strong><span>{{ configurationResult.message }}</span></div>
    <div v-if="configurationError" class="inline-result error" role="alert"><strong>配置操作失败</strong><span>{{ configurationError }}，请检查连接参数后重试。</span></div>
  </section>

  <section class="panel database-sources-panel">
    <div class="panel-title"><div><span>02</span><h2>业务数据源</h2></div><p>状态卡不显示账号和密码</p></div>
    <div class="database-source-list">
      <article v-for="source in status?.data_sources || []" :key="source.source">
        <div><strong>{{ source.label }}</strong><span :class="source.enabled && source.configured ? 'ready' : 'muted'">{{ !source.enabled ? '未启用' : source.configured ? '已配置' : '配置不完整' }}</span></div>
        <dl><div><dt>服务器</dt><dd>{{ source.host ? `${source.host}:${source.port}` : '--' }}</dd></div><div><dt>数据库</dt><dd>{{ source.database || '--' }}</dd></div></dl>
        <small v-if="source.uses_primary_fallback" class="source-warning">当前兼容回退到生产库；正式环境请配置专用连接。</small>
        <small v-else>已使用独立连接配置</small>
      </article>
    </div>
  </section>

  <div v-if="statusError" class="global-error" role="alert"><strong>状态读取失败</strong><span>{{ statusError }}，请重新检查数据库状态。</span></div>

  <section class="db-status-grid" aria-label="数据库状态">
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
    <form class="connection-form" @submit.prevent="runConnectionTest">
      <label><span>服务器地址</span><input v-model="connection.host" name="test_host" required placeholder="例如：192.168.1.20…" autocomplete="off" spellcheck="false" /></label>
      <label><span>端口</span><input v-model.number="connection.port" name="test_port" type="number" inputmode="numeric" autocomplete="off" min="1" max="65535" required /></label>
      <label><span>数据库名称</span><input v-model="connection.database" name="test_database" required autocomplete="off" spellcheck="false" /></label>
      <label><span>只读账号</span><input v-model="connection.username" name="test_username" required autocomplete="username" spellcheck="false" /></label>
      <label><span>密码</span><input v-model="connection.password" name="test_password" type="password" required autocomplete="current-password" /></label>
      <label class="driver-field"><span>ODBC 驱动</span><input v-model="connection.driver" name="test_driver" required autocomplete="off" spellcheck="false" /></label>
      <div class="connection-options">
        <label class="check-label"><input v-model="connection.encrypt" name="test_encrypt" type="checkbox" /><span>加密连接</span></label>
        <label class="check-label"><input v-model="connection.trust_server_certificate" name="test_trust_certificate" type="checkbox" /><span>信任服务器证书</span></label>
      </div>
      <button class="primary test-button" type="submit" :disabled="testing">
        <span v-if="testing" class="spinner"></span>{{ testing ? '正在测试' : '测试连接' }}
      </button>
    </form>
    <div v-if="testResult" class="inline-result success" role="status" aria-live="polite"><strong>连接成功</strong><span>{{ testResult.database }} · {{ formatCell(testResult.elapsed_ms) }} ms · 凭据未保存</span></div>
    <div v-if="testError" class="inline-result error" role="alert"><strong>连接失败</strong><span>{{ testError }}，请核对地址、账号和网络后重试。</span></div>
  </section>

  <section class="panel mapping-panel">
    <div class="panel-title"><div><span>04</span><h2>生产查询映射</h2></div><button class="text-button" :disabled="loadingStatus" @click="loadStatus">重新检查</button></div>
    <div class="mapping-list">
      <div v-for="item in status?.query_mappings || []" :key="item.filename" class="mapping-row">
        <span class="mapping-icon" :class="{ ready: item.ready }">{{ item.ready ? '✓' : '!' }}</span>
        <div><strong>{{ item.label }}</strong><small>{{ item.filename }}</small></div>
        <span class="mapping-state" :class="{ ready: item.ready }">{{ item.ready ? '已映射' : '等待实际 SQL' }}</span>
      </div>
    </div>
  </section>

  <section class="panel preview-panel">
    <div class="panel-title"><div><span>05</span><h2>固定数据集预览</h2></div><p>最多返回100条，不支持自定义SQL</p></div>
    <form class="preview-form" @submit.prevent="runPreview">
      <label><span>数据集</span><AppSelect v-model="preview.dataset"><option value="workshop">车间生产明细</option><option value="summary">汇总基础数据</option><option value="equipment">设备生产/能耗</option></AppSelect></label>
      <label><span>生产日期</span><AppDatePicker v-model="preview.report_date" required /></label>
      <label><span>工程号</span><input v-model="preview.project_code" name="preview_project_code" autocomplete="off" placeholder="全部工程…" /></label>
      <label><span>船号</span><input v-model="preview.ship_no" name="preview_ship_no" autocomplete="off" placeholder="全部船号…" /></label>
      <label><span>返回条数</span><input v-model.number="preview.limit" name="preview_limit" type="number" inputmode="numeric" autocomplete="off" min="1" max="100" /></label>
      <button class="primary" type="submit" :disabled="previewing"><span v-if="previewing" class="spinner"></span>{{ previewing ? '查询中' : '预览数据' }}</button>
    </form>
    <div v-if="previewError" class="inline-result error" role="alert"><strong>预览失败</strong><span>{{ previewError }}，请调整筛选条件后重试。</span></div>
    <div v-if="previewResult" class="preview-result">
      <div class="preview-summary"><strong>{{ modeLabel(previewResult.mode) }}</strong><span>返回 {{ previewResult.returned }} / {{ previewResult.total }} 条{{ previewResult.truncated ? '（已截断）' : '' }}</span></div>
      <div class="history-wrap">
        <table v-content-table class="history-table preview-table">
          <thead><tr><th v-for="column in previewColumns" :key="column">{{ labelFor(column) }}</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in previewResult.items" :key="row.task_id || index"><td v-for="column in previewColumns" :key="column">{{ formatCell(row[column]) }}</td></tr>
            <tr v-if="!previewResult.items.length"><td :colspan="previewColumns.length || 1" class="table-empty">当前条件下暂无数据</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>

  <section class="panel sql-panel">
    <div class="panel-title"><div><span>06</span><h2>SQL 工作台</h2></div><p>仅允许单条 SELECT / CTE，只读且限制返回行数</p></div>
    <div class="workbench-guard" :class="{ enabled: status?.sql_workbench_enabled }">
      <div>
        <strong>{{ status?.sql_workbench_enabled ? '执行功能已启用' : '执行功能当前关闭' }}</strong>
        <span v-if="!status?.sql_workbench_enabled">管理员需在服务器配置中显式开启，语法校验仍可使用。</span>
        <span v-else>执行时必须提供管理员令牌，并使用服务器配置的只读数据库账号。</span>
      </div>
      <small>最大返回 {{ status?.sql_workbench_max_rows || 200 }} 行 · 禁止 INSERT / UPDATE / DELETE / DDL / EXEC</small>
    </div>
    <div class="sql-toolbar">
      <span>示例：</span>
      <button type="button" @click="setSqlExample('select')">基础查询</button>
      <button type="button" @click="setSqlExample('aggregate')">分组汇总</button>
      <button type="button" @click="setSqlExample('cte')">CTE查询</button>
    </div>
    <div class="sql-editor-wrap">
      <div class="sql-editor-head"><span>T-SQL Editor</span><small>SQL内容不会写入普通业务日志</small></div>
      <textarea v-model="workbench.sql" class="sql-editor" spellcheck="false" aria-label="SQL编辑器"></textarea>
    </div>
    <div class="sql-actions">
      <label><span>返回上限</span><input v-model.number="workbench.maxRows" name="sql_max_rows" type="number" inputmode="numeric" autocomplete="off" min="1" :max="status?.sql_workbench_max_rows || 200" /></label>
      <label v-if="status?.sql_workbench_requires_token" class="token-field"><span>管理员令牌</span><input v-model="workbench.token" name="sql_admin_token" type="password" autocomplete="off" placeholder="执行查询时需要…" /></label>
      <button type="button" :disabled="validatingSql" @click="validateSql"><span v-if="validatingSql" class="spinner dark"></span>{{ validatingSql ? '校验中' : '校验并格式化' }}</button>
      <button class="primary" type="button" :disabled="executingSql || !status?.sql_workbench_enabled" @click="executeSql"><span v-if="executingSql" class="spinner"></span>{{ executingSql ? '执行中' : '执行查询' }}</button>
    </div>
    <div v-if="sqlValidation" class="inline-result success" role="status" aria-live="polite"><strong>只读校验通过</strong><span>表：{{ sqlValidation.tables.join(', ') || '--' }} · 字段：{{ sqlValidation.columns.length }}</span></div>
    <div v-if="sqlError" class="inline-result error" role="alert"><strong>SQL未执行</strong><span>{{ sqlError }}，请修正语句后重试。</span></div>
    <div v-if="sqlResult" class="sql-result">
      <div class="preview-summary"><strong>查询成功</strong><span>{{ sqlResult.returned }} 行 · {{ formatCell(sqlResult.elapsed_ms) }} ms{{ sqlResult.truncated ? ' · 结果已截断' : '' }}</span></div>
      <div class="history-wrap">
        <table v-content-table class="history-table preview-table">
          <thead><tr><th v-for="column in sqlResultColumns" :key="column">{{ column }}</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in sqlResult.items" :key="index"><td v-for="column in sqlResultColumns" :key="column">{{ formatCell(row[column]) }}</td></tr>
            <tr v-if="!sqlResult.items.length"><td :colspan="sqlResultColumns.length || 1" class="table-empty">查询成功，结果集为空</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
  </div>
</template>

<style scoped>
.database-page > .db-status-grid,
.database-page > .global-error { margin-bottom:0; }
.configuration-loading { padding:34px 24px; color:var(--muted); }
.configuration-switches { display:flex; align-items:flex-end; gap:22px; padding:20px 24px; border-bottom:1px solid var(--line); }
.configuration-switches > label:first-child { min-width:190px; }.configuration-switches label > span,.saved-source-grid label > span { display:block; margin-bottom:6px; color:var(--text-color-2); font-size:10px; font-weight:700; }
.configuration-switches .check-label,.saved-connection-options .check-label { display:flex; align-items:center; gap:7px; }.configuration-switches .check-label span,.saved-connection-options .check-label span { margin:0; }
.saved-source-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; padding:20px 24px; }
.saved-source-grid fieldset { min-width:0; display:grid; grid-template-columns:minmax(0,1fr) 92px; gap:11px; margin:0; padding:16px; border:1px solid var(--theme-border-color); }
.saved-source-grid legend { width:100%; display:flex; flex-direction:column; gap:3px; padding:0 7px; color:var(--navy); }.saved-source-grid legend strong { font-size:13px; }.saved-source-grid legend small { color:var(--muted); font-size:9px; font-weight:400; }
.saved-source-grid fieldset > label:nth-of-type(3),.saved-source-grid fieldset > label:nth-of-type(4),.saved-source-grid fieldset > label:nth-of-type(5),.saved-source-grid fieldset > label:nth-of-type(6),.saved-connection-options { grid-column:1/-1; }
.saved-connection-options { display:flex; flex-wrap:wrap; gap:12px; padding-top:3px; }
.configuration-actions { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:0 24px 24px; }.configuration-actions > span { color:var(--muted); font-size:10px; }
.database-source-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; padding:20px 24px 24px; }
.database-source-list article { min-width:0; padding:16px; border:1px solid var(--theme-border-color); background:rgb(var(--info-color) / .1); }
.database-source-list article > div { display:flex; align-items:center; justify-content:space-between; gap:10px; }
.database-source-list strong { color:var(--navy); font-size:13px; }
.database-source-list article > div > span { padding:3px 7px; border-radius:999px; font-size:9px; white-space:nowrap; }
.database-source-list .ready { color:rgb(var(--success-color)); background:rgb(var(--success-color) / .1); }
.database-source-list .muted { color:var(--text-color-2); background:rgb(var(--warning-color) / .1); }
.database-source-list dl { margin:14px 0 10px; }
.database-source-list dl div { display:grid; grid-template-columns:58px minmax(0,1fr); gap:8px; padding:5px 0; border-top:1px solid var(--theme-border-color); font-size:10px; }
.database-source-list dt { color:var(--muted); }.database-source-list dd { overflow:hidden; margin:0; color:var(--text-color-2); text-overflow:ellipsis; white-space:nowrap; }
.database-source-list small { color:var(--text-color-3); line-height:1.5; }.database-source-list .source-warning { color:rgb(var(--warning-color)); }
@media (max-width:1100px) { .saved-source-grid { grid-template-columns:1fr; } }
@media (max-width:900px) { .database-source-list { grid-template-columns:1fr; }.configuration-switches,.configuration-actions { align-items:stretch; flex-direction:column; } }
</style>
