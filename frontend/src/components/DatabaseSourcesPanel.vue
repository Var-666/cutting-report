<script setup lang="ts">
import type { DatabaseSourceRequestWritable, DatabaseSourceView, ConnectionTestResult } from '../api/generated/types.gen'
import { computed, onMounted, ref, watch } from 'vue'
import { NButton, NCheckbox, NInput, NInputNumber, NModal, NTag } from 'naive-ui'
import AppReportTable from './AppReportTable.vue'
import { listDatabaseSources, getDatabaseSource, saveDatabaseSource, setDatabaseSourceEnabled, deleteDatabaseSource, testDatabaseSource } from '../api/configuration.ts'
import { toError } from '../utils/errors'
import { useConfirm } from '../composables/useConfirm.ts'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.ts'

type Draft = Required<DatabaseSourceRequestWritable>
const sources = ref<DatabaseSourceView[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const editorError = ref('')
const result = ref<ConnectionTestResult | null>(null)
const showEditor = ref(false)
const editing = ref(false)
const passwordConfigured = ref(false)
const dirty = ref(false)
const confirm = useConfirm()
useUnsavedChanges(dirty, '数据源连接参数尚未保存，确定离开吗？')
const columns = [
  { key: 'c1', title: '名称 / ID', width: 180, align: 'left' as const },
  { key: 'c2', title: '数据库类型', width: 120, align: 'left' as const },
  { key: 'c3', title: '服务器 / 数据库', width: 230, align: 'left' as const },
  { key: 'c4', title: '状态', width: 140, align: 'left' as const },
  { key: 'c5', title: '操作', width: 300, align: 'center' as const },
]
function defaults(engine: Draft['engine'] = 'sqlserver'): Draft {
  return { id: '', name: '', engine, enabled: true, host: '', port: engine === 'mysql' ? 3306 : 1433,
    database: '', username: '', password: '', connect_timeout: 5, query_timeout: 120,
    driver: 'ODBC Driver 18 for SQL Server', encrypt: true, trust_server_certificate: true, legacy_tls: false,
    pool_size: 4, charset: 'utf8mb4', readonly: true, isolation_level: 'REPEATABLE READ' }
}
const draft = ref<Draft>(defaults())
watch(draft, () => { result.value = null }, { deep: true })
const title = computed(() => editing.value ? '编辑数据源' : '新增数据源')
function fromSource(source: DatabaseSourceView): Draft {
  const { password_configured, configured, references, ...fields } = source
  return { ...defaults(source.engine), ...fields, password: '' }
}
async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try { sources.value = (await listDatabaseSources()).items }
  catch (caught) { error.value = toError(caught).message }
  finally { loading.value = false }
}
function open(source?: DatabaseSourceView) {
  draft.value = source ? fromSource(source) : defaults()
  editing.value = Boolean(source)
  passwordConfigured.value = source?.password_configured ?? false
  editorError.value = ''
  result.value = null
  dirty.value = false
  showEditor.value = true
}
function changeEngine(engine: Draft['engine']) {
  draft.value.engine = engine
  if (engine !== 'sqlserver') draft.value.legacy_tls = false
  draft.value.port = engine === 'mysql' ? 3306 : 1433
  result.value = null
  dirty.value = true
}
async function close() {
  if (busy.value || (dirty.value && !await confirm('连接参数尚未保存，确定关闭吗？'))) return
  showEditor.value = false
  draft.value.password = ''
  dirty.value = false
}
async function save() {
  if (busy.value) return
  busy.value = true
  editorError.value = ''
  result.value = null
  try {
    await saveDatabaseSource({ ...draft.value, password: draft.value.password || null }, !editing.value)
    dirty.value = false
    draft.value.password = ''
    showEditor.value = false
    await load()
  } catch (caught) { editorError.value = toError(caught).message }
  finally { busy.value = false }
}
async function testConnection(source?: DatabaseSourceView) {
  if (busy.value) return
  busy.value = true
  if (source) error.value = ''
  else editorError.value = ''
  result.value = null
  try {
    // Read the latest saved profile for row tests; passwords stay server-side.
    const payload = source ? fromSource(await getDatabaseSource(source.id)) : { ...draft.value }
    result.value = await testDatabaseSource({ ...payload, password: payload.password || null })
  } catch (caught) {
    if (source) error.value = toError(caught).message
    else editorError.value = toError(caught).message
  } finally { busy.value = false }
}
async function toggle(source: DatabaseSourceView) {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await setDatabaseSourceEnabled(source.id, !source.enabled); await load() }
  catch (caught) { error.value = toError(caught).message }
  finally { busy.value = false }
}
async function remove(source: DatabaseSourceView) {
  if (busy.value || !await confirm(`删除数据源“${source.name}”？`)) return
  if (busy.value) return
  busy.value = true
  error.value = ''
  try { await deleteDatabaseSource(source.id); await load() }
  catch (caught) { error.value = toError(caught).message }
  finally { busy.value = false }
}
onMounted(load)
</script>

<template>
  <section class="panel sources-panel" :aria-busy="loading || busy">
    <div class="sources-heading">
      <div><h2>其他数据源</h2><p>登记 SQL Server 或 MySQL 连接；业务接入后由对应模块使用。</p></div>
      <NButton type="primary" :disabled="loading || busy" @click="open()">新增数据源</NButton>
    </div>
    <div v-if="error" class="source-message error" role="alert">{{ error }} <NButton text :disabled="loading || busy" @click="load">重新加载</NButton></div>
    <div v-if="result && !showEditor" class="source-message" role="status">{{ result.database }} · {{ result.elapsed_ms }} ms · {{ result.message }}</div>
    <AppReportTable :items="sources" :columns="columns" :row-key="row => row.id" :loading="loading" :paginate="false">
      <template #c1="{ row }"><strong>{{ row.name }}</strong><small class="source-secondary">{{ row.id }}</small></template>
      <template #c2="{ row }">{{ row.engine === 'mysql' ? 'MySQL' : 'SQL Server' }}</template>
      <template #c3="{ row }">{{ row.host }}:{{ row.port }}<small class="source-secondary">{{ row.database }}</small></template>
      <template #c4="{ row }"><NTag :type="row.enabled ? 'success' : 'default'" size="small">{{ row.enabled ? '已启用' : '已停用' }}</NTag><small class="source-secondary">{{ row.configured ? '参数已配置' : '缺少密码' }}</small></template>
      <template #c5="{ row }"><div class="source-actions">
        <NButton size="small" :disabled="busy" @click="testConnection(row)">测试连接</NButton>
        <NButton size="small" :disabled="busy" @click="open(row)">编辑</NButton>
        <NButton size="small" :disabled="busy" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</NButton>
        <NButton size="small" :disabled="busy || row.references.length > 0" @click="remove(row)">删除</NButton>
      </div><small v-if="row.references.length" class="source-secondary">引用：{{ row.references.join('、') }}</small></template>
      <template #empty>{{ loading ? '正在读取数据源…' : '暂无其他数据源，点击“新增数据源”登记连接。' }}</template>
    </AppReportTable>
    <NModal :show="showEditor" preset="card" :title="title" class="source-modal" :style="{ width: 'min(720px, calc(100vw - 32px))' }" :mask-closable="false" :close-on-esc="!busy" :closable="!busy" @update:show="value => { if (!value) close() }">
      <form class="source-form" @submit.prevent="save" @input.capture="dirty = true; result = null" @change.capture="dirty = true; result = null">
        <fieldset :disabled="busy" class="source-fields">
          <label><span>数据源 ID</span><NInput v-model:value="draft.id" :disabled="editing || busy" :input-props="{ 'aria-label': '数据源 ID', required: true, pattern: '[a-z][a-z0-9_-]{0,63}' }" placeholder="例如 quality" /></label>
          <label><span>名称</span><NInput :disabled="busy" v-model:value="draft.name" :maxlength="128" :input-props="{ 'aria-label': '名称', required: true }" /></label>
          <label><span>数据库类型</span><AppSelect aria-label="数据库类型" :disabled="busy" :model-value="draft.engine" @update:model-value="changeEngine"><option value="sqlserver">SQL Server</option><option value="mysql">MySQL</option></AppSelect></label>
          <label class="source-checkbox"><NCheckbox :disabled="busy" v-model:checked="draft.enabled" @update:checked="dirty = true" >启用数据源</NCheckbox></label>
          <label><span>服务器地址</span><NInput :disabled="busy" v-model:value="draft.host" :input-props="{ 'aria-label': '服务器地址', required: true, autocomplete: 'off' }" /></label>
          <label><span>端口</span><NInputNumber :disabled="busy" v-model:value="draft.port" :input-props="{ 'aria-label': '端口' }" :min="1" :max="65535" :precision="0" @update:value="dirty = true" /></label>
          <label><span>数据库名称</span><NInput :disabled="busy" v-model:value="draft.database" :input-props="{ 'aria-label': '数据库名称', required: true }" /></label>
          <label><span>只读账号</span><NInput :disabled="busy" v-model:value="draft.username" :input-props="{ 'aria-label': '只读账号', required: true, autocomplete: 'off' }" /></label>
          <label class="source-full"><span>密码</span><NInput :disabled="busy" v-model:value="draft.password" type="password" autocomplete="new-password" :placeholder="passwordConfigured ? '已配置，留空保留原密码' : '请输入密码'" :input-props="{ 'aria-label': '密码', required: draft.enabled && !passwordConfigured, autocomplete: 'new-password' }" /></label>
          <label><span>连接超时（秒）</span><NInputNumber :disabled="busy" v-model:value="draft.connect_timeout" :input-props="{ 'aria-label': '连接超时（秒）' }" :min="1" :max="60" :precision="0" @update:value="dirty = true" /></label>
          <label><span>查询超时（秒）</span><NInputNumber :disabled="busy" v-model:value="draft.query_timeout" :input-props="{ 'aria-label': '查询超时（秒）' }" :min="1" :max="300" :precision="0" @update:value="dirty = true" /></label>
          <template v-if="draft.engine === 'sqlserver'">
            <label class="source-full"><span>ODBC 驱动</span><NInput :disabled="busy" v-model:value="draft.driver" :input-props="{ 'aria-label': 'ODBC 驱动', required: true }" /></label>
            <label class="source-checkbox"><NCheckbox :disabled="busy" v-model:checked="draft.encrypt" @update:checked="dirty = true" >加密连接</NCheckbox></label>
            <label class="source-checkbox"><NCheckbox :disabled="busy" v-model:checked="draft.trust_server_certificate" @update:checked="dirty = true" >信任服务器证书</NCheckbox></label>
            <label class="source-full source-checkbox"><NCheckbox :disabled="busy" v-model:checked="draft.legacy_tls" @update:checked="dirty = true">兼容旧版 SQL Server TLS</NCheckbox></label>
            <p v-if="draft.legacy_tls" class="source-full source-note">仅此数据源的批量只读查询允许旧 TLS 和弱签名算法，在隔离进程中执行；不改变其他连接。仅在无法更新旧服务器时启用。</p>
          </template>
          <template v-else>
            <label><span>连接池大小</span><NInputNumber :disabled="busy" v-model:value="draft.pool_size" :input-props="{ 'aria-label': '连接池大小' }" :min="1" :max="4" :precision="0" @update:value="dirty = true" /></label>
            <label><span>字符集</span><NInput :disabled="busy" v-model:value="draft.charset" :input-props="{ 'aria-label': '字符集', required: true, pattern: '[a-zA-Z0-9_]{1,32}' }" /></label>
            <p class="source-full source-note">使用只读事务和 REPEATABLE READ 一致性快照。</p>
          </template>
        </fieldset>
        <p v-if="editorError" class="source-message error" role="alert">{{ editorError }}</p>
        <p v-if="result" class="source-message" role="status">{{ result.elapsed_ms }} ms · {{ result.message }}</p>
        <div class="source-footer"><NButton :disabled="busy" @click="testConnection()">测试连接</NButton><NButton :disabled="busy" @click="close">取消</NButton><NButton attr-type="submit" type="primary" :loading="busy" :disabled="busy">保存</NButton></div>
      </form>
    </NModal>
  </section>
</template>

<style scoped>
.sources-heading { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:24px; }
.sources-heading h2 { margin:0; color:var(--text-color-1); font-size:var(--font-section-title); }
.sources-heading p,.source-note { margin:8px 0 0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
.source-secondary { display:block; margin-top:4px; color:var(--text-color-3); overflow-wrap:anywhere; }
.source-actions,.source-footer { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.source-fields { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; min-width:0; margin:0; padding:0; border:0; }
.source-fields label { min-width:0; }
.source-fields label > span { display:block; margin-bottom:8px; color:var(--text-color-2); font-size:var(--font-auxiliary); }
.source-checkbox { display:flex; align-items:center; gap:8px; }
.source-full { grid-column:1/-1; }
.source-footer { justify-content:flex-end; margin-top:24px; }
.source-message { margin:0; padding:12px 24px; color:var(--text-color-2); overflow-wrap:anywhere; }
.source-form .source-message { padding:12px 0; }
.error { color:rgb(var(--error-color)); }
@media(max-width:560px) { .sources-heading { align-items:flex-start; flex-direction:column; padding:16px; }.source-fields { grid-template-columns:minmax(0,1fr); }.source-full { grid-column:1; }.source-footer { justify-content:flex-start; } }
</style>
