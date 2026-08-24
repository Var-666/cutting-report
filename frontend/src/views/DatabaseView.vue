<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  executeWorkbenchSql,
  getDatabaseStatus,
  previewDatabaseDataset,
  testDatabaseConnection,
  validateWorkbenchSql,
} from '../api/reports'

const status = ref(null)
const loadingStatus = ref(false)
const statusError = ref('')
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
const sourceName = computed(() => ({ demo: '演示数据', excel: 'TASK.xlsx', sqlserver: 'SQL Server' }[status.value?.mode] || '正在检查') )
const sourceDescription = computed(() => {
  if (status.value?.mode === 'excel') {
    const usable = status.value.excel_status?.reportable_completed_count || 0
    const excluded = status.value.excel_status?.excluded_negative_work_time_count || 0
    return `${usable} 条可用于报表${excluded ? ` · 已剔除 ${excluded} 条负工时` : ''}`
  }
  if (status.value?.mode === 'sqlserver') return '生产查询模式'
  return '尚未连接生产库'
})
const modeLabel = (mode) => ({ demo: '演示数据', excel: 'TASK.xlsx 本地表', sqlserver: 'SQL Server' }[mode] || '未知数据源')

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
  if (typeof value === 'number') return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 3 }).format(value)
  return value
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

onMounted(loadStatus)
</script>

<template>
  <section class="database-hero">
    <div>
      <span class="eyebrow">DATABASE ADMINISTRATION</span>
      <h2>数据库管理</h2>
      <p>当前可使用本地 TASK.xlsx 生成报表，并保留 SQL Server 连接和数据映射能力。</p>
    </div>
    <div class="security-badge"><strong>只读模式</strong><span>仅开放 SELECT / CTE</span></div>
  </section>

  <div v-if="statusError" class="global-error"><strong>状态读取失败</strong><span>{{ statusError }}</span></div>

  <section class="db-status-grid" aria-label="数据库状态">
    <article class="db-status-card">
      <span>当前数据源</span>
      <strong>{{ sourceName }}</strong>
      <small>{{ sourceDescription }}</small>
    </article>
    <article class="db-status-card">
      <span>{{ status?.mode === 'excel' ? '本地任务数据' : 'ODBC 驱动' }}</span>
      <strong :class="status?.mode === 'excel' || status?.pyodbc_installed ? 'ok-text' : 'warn-text'">{{ status?.mode === 'excel' ? `${status?.excel_status?.row_count || 0} 行` : status?.pyodbc_installed ? '已安装' : '未安装' }}</strong>
      <small>{{ status?.mode === 'excel' ? `${status?.excel_status?.date_min || '--'} 至 ${status?.excel_status?.date_max || '--'}` : status?.driver || '正在检查' }}</small>
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
    <div class="panel-title"><div><span>01</span><h2>连接测试</h2></div><p>凭据仅用于本次测试，不会保存</p></div>
    <form class="connection-form" @submit.prevent="runConnectionTest">
      <label><span>服务器地址</span><input v-model="connection.host" required placeholder="例如 192.168.1.20" autocomplete="off" /></label>
      <label><span>端口</span><input v-model.number="connection.port" type="number" min="1" max="65535" required /></label>
      <label><span>数据库名称</span><input v-model="connection.database" required autocomplete="off" /></label>
      <label><span>只读账号</span><input v-model="connection.username" required autocomplete="username" /></label>
      <label><span>密码</span><input v-model="connection.password" type="password" required autocomplete="current-password" /></label>
      <label class="driver-field"><span>ODBC 驱动</span><input v-model="connection.driver" required /></label>
      <div class="connection-options">
        <label class="check-label"><input v-model="connection.encrypt" type="checkbox" /><span>加密连接</span></label>
        <label class="check-label"><input v-model="connection.trust_server_certificate" type="checkbox" /><span>信任服务器证书</span></label>
      </div>
      <button class="primary test-button" type="submit" :disabled="testing">
        <span v-if="testing" class="spinner"></span>{{ testing ? '正在测试' : '测试连接' }}
      </button>
    </form>
    <div v-if="testResult" class="inline-result success"><strong>连接成功</strong><span>{{ testResult.database }} · {{ testResult.elapsed_ms }} ms · 凭据未保存</span></div>
    <div v-if="testError" class="inline-result error"><strong>连接失败</strong><span>{{ testError }}</span></div>
  </section>

  <section class="panel mapping-panel">
    <div class="panel-title"><div><span>02</span><h2>生产查询映射</h2></div><button class="text-button" :disabled="loadingStatus" @click="loadStatus">重新检查</button></div>
    <div class="mapping-list">
      <div v-for="item in status?.query_mappings || []" :key="item.filename" class="mapping-row">
        <span class="mapping-icon" :class="{ ready: item.ready }">{{ item.ready ? '✓' : '!' }}</span>
        <div><strong>{{ item.label }}</strong><small>{{ item.filename }}</small></div>
        <span class="mapping-state" :class="{ ready: item.ready }">{{ item.ready ? '已映射' : '等待实际 SQL' }}</span>
      </div>
    </div>
  </section>

  <section class="panel preview-panel">
    <div class="panel-title"><div><span>03</span><h2>固定数据集预览</h2></div><p>最多返回100条，不支持自定义SQL</p></div>
    <form class="preview-form" @submit.prevent="runPreview">
      <label><span>数据集</span><select v-model="preview.dataset"><option value="workshop">车间生产明细</option><option value="summary">汇总基础数据</option><option value="equipment">设备生产/能耗</option></select></label>
      <label><span>生产日期</span><input v-model="preview.report_date" type="date" required /></label>
      <label><span>工程号</span><input v-model="preview.project_code" placeholder="全部" /></label>
      <label><span>船号</span><input v-model="preview.ship_no" placeholder="全部" /></label>
      <label><span>返回条数</span><input v-model.number="preview.limit" type="number" min="1" max="100" /></label>
      <button class="primary" type="submit" :disabled="previewing"><span v-if="previewing" class="spinner"></span>{{ previewing ? '查询中' : '预览数据' }}</button>
    </form>
    <div v-if="previewError" class="inline-result error"><strong>预览失败</strong><span>{{ previewError }}</span></div>
    <div v-if="previewResult" class="preview-result">
      <div class="preview-summary"><strong>{{ modeLabel(previewResult.mode) }}</strong><span>返回 {{ previewResult.returned }} / {{ previewResult.total }} 条{{ previewResult.truncated ? '（已截断）' : '' }}</span></div>
      <div class="history-wrap">
        <table class="history-table preview-table">
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
    <div class="panel-title"><div><span>04</span><h2>SQL 工作台</h2></div><p>仅允许单条 SELECT / CTE，只读且限制返回行数</p></div>
    <div class="workbench-guard" :class="{ enabled: status?.sql_workbench_enabled }">
      <div>
        <strong>{{ status?.sql_workbench_enabled ? '执行功能已启用' : '执行功能当前关闭' }}</strong>
        <span v-if="!status?.sql_workbench_enabled">管理员需在服务器配置中显式开启，语法校验仍可使用。</span>
        <span v-else-if="status?.mode === 'excel'">查询在 TASK.xlsx 的本地只读副本中执行，不会修改原文件。</span>
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
      <div class="sql-editor-head"><span>{{ status?.mode === 'excel' ? 'T-SQL Editor · 本地兼容模式' : 'T-SQL Editor' }}</span><small>SQL内容不会写入普通业务日志</small></div>
      <textarea v-model="workbench.sql" class="sql-editor" spellcheck="false" aria-label="SQL编辑器"></textarea>
    </div>
    <div class="sql-actions">
      <label><span>返回上限</span><input v-model.number="workbench.maxRows" type="number" min="1" :max="status?.sql_workbench_max_rows || 200" /></label>
      <label v-if="status?.sql_workbench_requires_token" class="token-field"><span>管理员令牌</span><input v-model="workbench.token" type="password" autocomplete="off" placeholder="执行查询时需要" /></label>
      <button type="button" :disabled="validatingSql" @click="validateSql"><span v-if="validatingSql" class="spinner dark"></span>{{ validatingSql ? '校验中' : '校验并格式化' }}</button>
      <button class="primary" type="button" :disabled="executingSql || !status?.sql_workbench_enabled" @click="executeSql"><span v-if="executingSql" class="spinner"></span>{{ executingSql ? '执行中' : '执行查询' }}</button>
    </div>
    <div v-if="sqlValidation" class="inline-result success"><strong>只读校验通过</strong><span>表：{{ sqlValidation.tables.join(', ') || '--' }} · 字段：{{ sqlValidation.columns.length }}</span></div>
    <div v-if="sqlError" class="inline-result error"><strong>SQL未执行</strong><span>{{ sqlError }}</span></div>
    <div v-if="sqlResult" class="sql-result">
      <div class="preview-summary"><strong>查询成功</strong><span>{{ sqlResult.returned }} 行 · {{ sqlResult.elapsed_ms }} ms{{ sqlResult.truncated ? ' · 结果已截断' : '' }}</span></div>
      <div class="history-wrap">
        <table class="history-table preview-table">
          <thead><tr><th v-for="column in sqlResultColumns" :key="column">{{ column }}</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in sqlResult.items" :key="index"><td v-for="column in sqlResultColumns" :key="column">{{ formatCell(row[column]) }}</td></tr>
            <tr v-if="!sqlResult.items.length"><td :colspan="sqlResultColumns.length || 1" class="table-empty">查询成功，结果集为空</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>
