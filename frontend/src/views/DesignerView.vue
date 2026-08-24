<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  deleteDesignerResource,
  listDesignerResources,
  previewDesignerQuery,
  saveDesignerResource,
  validateDesignerSql,
} from '../api/reports'

const emit = defineEmits(['definitions-change'])
const tabs = [
  { id: 'queries', label: '查询任务' },
  { id: 'definitions', label: '报表定义' },
  { id: 'templates', label: '模板设计器' },
]
const activeTab = ref('queries')
const adminToken = ref('')
const queryTasks = ref([])
const templates = ref([])
const definitions = ref([])
const queryDraft = ref(null)
const templateDraft = ref(null)
const definitionDraft = ref(null)
const queryValidation = ref(null)
const queryPreview = ref(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })

const clone = (value) => JSON.parse(JSON.stringify(value))
const selectedDefinitionQuery = computed(() => queryTasks.value.find((item) => item.query_task_id === definitionDraft.value?.query_task_id))
const definitionFields = computed(() => selectedDefinitionQuery.value?.columns || [])

function blankQuery() {
  return {
    query_task_id: '', code: '', name: '', description: '', enabled: true, is_builtin: false,
    sql_text: `SELECT
    t.[OP_DATE] AS production_date,
    t.[TASK_ID] AS task_id,
    TRY_CONVERT(decimal(18, 3), t.[CUT_LENGTH]) AS cut_length
FROM [dbo].[task] AS t
WHERE t.[STATUS] = 2
  AND t.[OP_DATE] >= :period_start
  AND t.[OP_DATE] < :period_end
ORDER BY t.[OP_DATE] DESC`,
    columns: [
      { field: 'production_date', label: '日期', type: 'date', unit: '' },
      { field: 'task_id', label: '任务号', type: 'text', unit: '' },
      { field: 'cut_length', label: '切割长度', type: 'number', unit: '米' },
    ],
  }
}

function blankTemplate(source = null) {
  const config = source?.config || {
    title_prefix: '切割生产', logo_text: 'PRODUCTION REPORT', primary_color: '#1f4e78',
    accent_color: '#56a3a6', background_color: '#eef3f7', layout: 'split',
    table_density: 'normal', chart_height: 300, show_meta: true,
  }
  return {
    template_id: '', code: source ? `${source.code}_copy` : '', name: source ? `${source.name}副本` : '',
    description: source?.description || '', enabled: true, is_builtin: false, config: clone(config),
  }
}

function blankDefinition() {
  const query = queryTasks.value.find((item) => !item.is_builtin && item.enabled)
  const template = templates.value.find((item) => item.enabled)
  const fields = query?.columns || []
  const firstNumber = fields.find((item) => item.type === 'number')
  return {
    definition_id: '', code: '', name: '', description: '', enabled: true, is_builtin: false,
    query_task_id: query?.query_task_id || '', template_id: template?.template_id || '', period_type: 'day',
    visual_config: {
      columns: fields.map((item) => item.field),
      metrics: firstNumber ? [{ field: firstNumber.field, label: firstNumber.label, aggregation: 'sum', unit: firstNumber.unit }] : [],
      chart: {
        type: 'bar', category_field: fields[0]?.field || '',
        value_fields: firstNumber ? [firstNumber.field] : [], title: '数据趋势',
      },
    },
  }
}

async function loadResources() {
  loading.value = true
  error.value = ''
  try {
    const [queries, templateData, definitionData] = await Promise.all([
      listDesignerResources('query-tasks', adminToken.value),
      listDesignerResources('templates', adminToken.value),
      listDesignerResources('definitions', adminToken.value),
    ])
    queryTasks.value = queries.items
    templates.value = templateData.items
    definitions.value = definitionData.items
    if (!queryDraft.value) queryDraft.value = blankQuery()
    if (!templateDraft.value) templateDraft.value = blankTemplate()
    if (!definitionDraft.value) definitionDraft.value = blankDefinition()
  } catch (exception) {
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

function chooseQuery(item) { queryDraft.value = clone(item); queryValidation.value = null; queryPreview.value = null; error.value = ''; notice.value = '' }
function chooseTemplate(item) { templateDraft.value = clone(item); error.value = ''; notice.value = '' }
function chooseDefinition(item) { definitionDraft.value = clone(item); error.value = ''; notice.value = '' }

function addColumn() { queryDraft.value.columns.push({ field: '', label: '', type: 'text', unit: '' }) }
function removeColumn(index) { queryDraft.value.columns.splice(index, 1) }
function addMetric() {
  const field = definitionFields.value.find((item) => item.type === 'number') || definitionFields.value[0]
  if (field) definitionDraft.value.visual_config.metrics.push({ field: field.field, label: field.label, aggregation: 'sum', unit: field.unit })
}
function removeMetric(index) { definitionDraft.value.visual_config.metrics.splice(index, 1) }
function toggleColumn(field, checked) {
  const columns = definitionDraft.value.visual_config.columns
  if (checked && !columns.includes(field)) columns.push(field)
  if (!checked) definitionDraft.value.visual_config.columns = columns.filter((item) => item !== field)
}
function toggleSeries(field, checked) {
  const fields = definitionDraft.value.visual_config.chart.value_fields
  if (checked && !fields.includes(field)) fields.push(field)
  if (!checked) definitionDraft.value.visual_config.chart.value_fields = fields.filter((item) => item !== field)
}
function syncDefinitionFields() {
  const fields = definitionFields.value
  definitionDraft.value.visual_config.columns = fields.map((item) => item.field)
  definitionDraft.value.visual_config.metrics = []
  definitionDraft.value.visual_config.chart.category_field = fields[0]?.field || ''
  definitionDraft.value.visual_config.chart.value_fields = fields.filter((item) => item.type === 'number').slice(0, 2).map((item) => item.field)
}

async function validateQuery() {
  error.value = ''; notice.value = ''; queryValidation.value = null
  try {
    queryValidation.value = await validateDesignerSql(queryDraft.value.sql_text, adminToken.value)
    queryDraft.value.sql_text = queryValidation.value.formatted_sql
    notice.value = `SQL只读校验通过，识别到 ${queryValidation.value.tables.length} 个数据表`
  } catch (exception) { error.value = exception.message }
}

async function saveResource(resource, draft, idKey) {
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const id = draft[idKey] || ''
    const saved = await saveDesignerResource(resource, draft, id, adminToken.value)
    if (resource === 'query-tasks') queryDraft.value = clone(saved)
    if (resource === 'templates') templateDraft.value = clone(saved)
    if (resource === 'definitions') definitionDraft.value = clone(saved)
    notice.value = '保存成功，配置版本已更新'
    await loadResources()
    if (resource === 'definitions') emit('definitions-change')
  } catch (exception) { error.value = exception.message } finally { saving.value = false }
}

async function removeResource(resource, draft, idKey) {
  const id = draft?.[idKey]
  if (!id || draft.is_builtin || !window.confirm(`确认删除“${draft.name}”？`)) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    await deleteDesignerResource(resource, id, adminToken.value)
    notice.value = '删除成功'
    if (resource === 'query-tasks') queryDraft.value = blankQuery()
    if (resource === 'templates') templateDraft.value = blankTemplate()
    if (resource === 'definitions') { definitionDraft.value = blankDefinition(); emit('definitions-change') }
    await loadResources()
  } catch (exception) { error.value = exception.message } finally { saving.value = false }
}

async function previewQuery() {
  if (!queryDraft.value.query_task_id) { error.value = '请先保存查询任务，再执行数据预览'; return }
  error.value = ''; queryPreview.value = null
  try {
    queryPreview.value = await previewDesignerQuery(queryDraft.value.query_task_id, { report_date: today, project_code: null, ship_no: null, parameters: {}, max_rows: 10 }, adminToken.value)
  } catch (exception) { error.value = exception.message }
}

onMounted(loadResources)
</script>

<template>
  <section class="designer-hero">
    <div><span class="eyebrow">REPORT DESIGN STUDIO</span><h2>报表定义与查询任务管理</h2><p>将只读 SQL、字段指标、图表和离线 HTML 模板组合为可发布的新报表。</p></div>
    <label class="designer-token"><span>管理员令牌</span><input v-model="adminToken" type="password" placeholder="SQL Server 模式需填写" @change="loadResources" /></label>
  </section>

  <section class="panel designer-shell">
    <div class="designer-tabs" role="tablist">
      <button v-for="tab in tabs" :key="tab.id" type="button" :class="{ active: activeTab === tab.id }" @click="activeTab = tab.id">{{ tab.label }}</button>
    </div>
    <div v-if="error" class="designer-message error">{{ error }}</div>
    <div v-if="notice" class="designer-message success">{{ notice }}</div>

    <div v-if="activeTab === 'queries'" class="designer-workspace">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>查询任务</strong><button type="button" @click="queryDraft = blankQuery()">新建</button></div>
        <button v-for="item in queryTasks" :key="item.query_task_id" type="button" class="designer-list-item" :class="{ active: queryDraft?.query_task_id === item.query_task_id }" @click="chooseQuery(item)">
          <strong>{{ item.name }}</strong><span>{{ item.code }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i>
        </button>
        <p v-if="!queryTasks.length && !loading">暂无查询任务</p>
      </aside>
      <div v-if="queryDraft" class="designer-editor">
        <div class="designer-editor-head"><div><span>QUERY TASK</span><h3>{{ queryDraft.query_task_id ? queryDraft.name : '新建查询任务' }}</h3></div><small v-if="queryDraft.is_builtin">系统内置，只读</small></div>
        <div class="designer-form-grid">
          <label><span>任务名称</span><input v-model="queryDraft.name" :disabled="queryDraft.is_builtin" /></label>
          <label><span>任务编码</span><input v-model="queryDraft.code" :disabled="queryDraft.is_builtin || !!queryDraft.query_task_id" placeholder="daily_quality" /></label>
          <label class="wide"><span>说明</span><input v-model="queryDraft.description" :disabled="queryDraft.is_builtin" /></label>
        </div>
        <label class="designer-block"><span>只读 T-SQL</span><textarea v-model="queryDraft.sql_text" class="designer-sql" spellcheck="false" :disabled="queryDraft.is_builtin"></textarea><small>支持 :period_start、:period_end、:report_date、:project_code、:ship_no 参数，执行时使用参数化查询。</small></label>
        <div class="designer-section-title"><strong>返回字段</strong><button type="button" :disabled="queryDraft.is_builtin" @click="addColumn">增加字段</button></div>
        <div class="designer-fields">
          <div v-for="(column, index) in queryDraft.columns" :key="index" class="designer-field-row">
            <input v-model="column.field" placeholder="field_name" :disabled="queryDraft.is_builtin" />
            <input v-model="column.label" placeholder="显示名称" :disabled="queryDraft.is_builtin" />
            <select v-model="column.type" :disabled="queryDraft.is_builtin"><option value="text">文本</option><option value="number">数值</option><option value="date">日期</option><option value="datetime">时间</option><option value="duration">时长</option></select>
            <input v-model="column.unit" placeholder="单位" :disabled="queryDraft.is_builtin" />
            <button type="button" :disabled="queryDraft.is_builtin" @click="removeColumn(index)">删除</button>
          </div>
        </div>
        <div class="designer-actions">
          <label class="switch-label"><input v-model="queryDraft.enabled" type="checkbox" :disabled="queryDraft.is_builtin" />启用</label>
          <button type="button" :disabled="queryDraft.is_builtin" @click="validateQuery">校验 SQL</button>
          <button type="button" :disabled="!queryDraft.query_task_id" @click="previewQuery">预览数据</button>
          <button class="primary" type="button" :disabled="saving || queryDraft.is_builtin" @click="saveResource('query-tasks', queryDraft, 'query_task_id')">保存任务</button>
          <button class="danger-button" type="button" :disabled="queryDraft.is_builtin" @click="removeResource('query-tasks', queryDraft, 'query_task_id')">删除</button>
        </div>
        <div v-if="queryPreview" class="designer-preview-table"><div>预览返回 {{ queryPreview.returned }} 行 · {{ queryPreview.mode }}</div><div class="history-wrap"><table><thead><tr><th v-for="column in queryPreview.columns" :key="column.field">{{ column.label }}</th></tr></thead><tbody><tr v-for="(row, index) in queryPreview.items" :key="index"><td v-for="column in queryPreview.columns" :key="column.field">{{ row[column.field] }}</td></tr></tbody></table></div></div>
      </div>
    </div>

    <div v-else-if="activeTab === 'definitions'" class="designer-workspace">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>报表定义</strong><button type="button" @click="definitionDraft = blankDefinition()">新建</button></div>
        <button v-for="item in definitions" :key="item.definition_id" type="button" class="designer-list-item" :class="{ active: definitionDraft?.definition_id === item.definition_id }" @click="chooseDefinition(item)"><strong>{{ item.name }}</strong><span>{{ item.is_builtin ? '内置报表' : item.query_task_name }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i></button>
      </aside>
      <div v-if="definitionDraft" class="designer-editor">
        <div class="designer-editor-head"><div><span>REPORT DEFINITION</span><h3>{{ definitionDraft.definition_id ? definitionDraft.name : '新建报表定义' }}</h3></div><small v-if="definitionDraft.is_builtin">系统内置定义保持现有专用管线</small></div>
        <div class="designer-form-grid">
          <label><span>报表名称</span><input v-model="definitionDraft.name" :disabled="definitionDraft.is_builtin" /></label>
          <label><span>定义编码</span><input v-model="definitionDraft.code" :disabled="definitionDraft.is_builtin || !!definitionDraft.definition_id" placeholder="quality_daily" /></label>
          <label><span>查询任务</span><select v-model="definitionDraft.query_task_id" :disabled="definitionDraft.is_builtin" @change="syncDefinitionFields"><option value="">请选择</option><option v-for="item in queryTasks.filter(row => row.enabled && !row.is_builtin)" :key="item.query_task_id" :value="item.query_task_id">{{ item.name }}</option></select></label>
          <label><span>报表模板</span><select v-model="definitionDraft.template_id" :disabled="definitionDraft.is_builtin"><option v-for="item in templates.filter(row => row.enabled)" :key="item.template_id" :value="item.template_id">{{ item.name }}</option></select></label>
          <label><span>周期类型</span><select v-model="definitionDraft.period_type" :disabled="definitionDraft.is_builtin"><option value="day">按日</option><option value="month">按月</option></select></label>
          <label class="wide"><span>说明</span><input v-model="definitionDraft.description" :disabled="definitionDraft.is_builtin" /></label>
        </div>
        <template v-if="!definitionDraft.is_builtin">
          <div class="designer-section-title"><strong>明细表字段</strong><span>选择导出表格需要显示的列</span></div>
          <div class="designer-check-grid"><label v-for="field in definitionFields" :key="field.field"><input type="checkbox" :checked="definitionDraft.visual_config.columns.includes(field.field)" @change="toggleColumn(field.field, $event.target.checked)" />{{ field.label }}</label></div>
          <div class="designer-section-title"><strong>核心指标</strong><button type="button" @click="addMetric">增加指标</button></div>
          <div class="designer-fields"><div v-for="(metric, index) in definitionDraft.visual_config.metrics" :key="index" class="designer-field-row metric-row"><select v-model="metric.field"><option v-for="field in definitionFields" :key="field.field" :value="field.field">{{ field.label }}</option></select><input v-model="metric.label" placeholder="指标名称" /><select v-model="metric.aggregation"><option value="sum">求和</option><option value="avg">平均</option><option value="count">计数</option><option value="max">最大值</option><option value="min">最小值</option></select><input v-model="metric.unit" placeholder="单位" /><button type="button" @click="removeMetric(index)">删除</button></div></div>
          <div class="designer-section-title"><strong>图表配置</strong></div>
          <div class="designer-form-grid">
            <label><span>图表标题</span><input v-model="definitionDraft.visual_config.chart.title" /></label>
            <label><span>图表类型</span><select v-model="definitionDraft.visual_config.chart.type"><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option><option value="bar_line">柱线组合</option></select></label>
            <label><span>分类轴字段</span><select v-model="definitionDraft.visual_config.chart.category_field"><option v-for="field in definitionFields" :key="field.field" :value="field.field">{{ field.label }}</option></select></label>
          </div>
          <div class="designer-check-grid"><label v-for="field in definitionFields.filter(item => item.type === 'number')" :key="field.field"><input type="checkbox" :checked="definitionDraft.visual_config.chart.value_fields.includes(field.field)" @change="toggleSeries(field.field, $event.target.checked)" />{{ field.label }}</label></div>
        </template>
        <div class="designer-actions"><label class="switch-label"><input v-model="definitionDraft.enabled" type="checkbox" :disabled="definitionDraft.is_builtin" />发布并启用</label><button class="primary" type="button" :disabled="saving || definitionDraft.is_builtin" @click="saveResource('definitions', definitionDraft, 'definition_id')">保存定义</button><button class="danger-button" type="button" :disabled="definitionDraft.is_builtin" @click="removeResource('definitions', definitionDraft, 'definition_id')">删除</button></div>
      </div>
    </div>

    <div v-else class="designer-workspace template-workspace">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>报表模板</strong><button type="button" @click="templateDraft = blankTemplate()">新建</button></div>
        <button v-for="item in templates" :key="item.template_id" type="button" class="designer-list-item" :class="{ active: templateDraft?.template_id === item.template_id }" @click="chooseTemplate(item)"><strong>{{ item.name }}</strong><span>{{ item.code }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i></button>
      </aside>
      <div v-if="templateDraft" class="designer-editor template-editor">
        <div class="designer-editor-head"><div><span>TEMPLATE DESIGNER</span><h3>{{ templateDraft.template_id ? templateDraft.name : '新建报表模板' }}</h3></div><button v-if="templateDraft.is_builtin" type="button" @click="templateDraft = blankTemplate(templateDraft)">复制为新模板</button></div>
        <div class="template-design-grid">
          <div>
            <div class="designer-form-grid">
              <label><span>模板名称</span><input v-model="templateDraft.name" :disabled="templateDraft.is_builtin" /></label>
              <label><span>模板编码</span><input v-model="templateDraft.code" :disabled="templateDraft.is_builtin || !!templateDraft.template_id" /></label>
              <label class="wide"><span>模板说明</span><input v-model="templateDraft.description" :disabled="templateDraft.is_builtin" /></label>
              <label><span>报告标题前缀</span><input v-model="templateDraft.config.title_prefix" :disabled="templateDraft.is_builtin" /></label>
              <label><span>页眉标识</span><input v-model="templateDraft.config.logo_text" :disabled="templateDraft.is_builtin" /></label>
              <label><span>主色</span><div class="color-field"><input v-model="templateDraft.config.primary_color" type="color" :disabled="templateDraft.is_builtin" /><code>{{ templateDraft.config.primary_color }}</code></div></label>
              <label><span>强调色</span><div class="color-field"><input v-model="templateDraft.config.accent_color" type="color" :disabled="templateDraft.is_builtin" /><code>{{ templateDraft.config.accent_color }}</code></div></label>
              <label><span>页面背景</span><div class="color-field"><input v-model="templateDraft.config.background_color" type="color" :disabled="templateDraft.is_builtin" /><code>{{ templateDraft.config.background_color }}</code></div></label>
              <label><span>内容布局</span><select v-model="templateDraft.config.layout" :disabled="templateDraft.is_builtin"><option value="split">图表与表格双栏</option><option value="chart_first">图表在前</option><option value="table_first">表格在前</option></select></label>
              <label><span>表格密度</span><select v-model="templateDraft.config.table_density" :disabled="templateDraft.is_builtin"><option value="compact">紧凑</option><option value="normal">标准</option><option value="comfortable">宽松</option></select></label>
              <label><span>图表高度 {{ templateDraft.config.chart_height }}px</span><input v-model.number="templateDraft.config.chart_height" type="range" min="220" max="520" step="10" :disabled="templateDraft.is_builtin" /></label>
            </div>
            <div class="designer-actions"><label class="switch-label"><input v-model="templateDraft.config.show_meta" type="checkbox" :disabled="templateDraft.is_builtin" />显示报告元信息</label><label class="switch-label"><input v-model="templateDraft.enabled" type="checkbox" :disabled="templateDraft.is_builtin" />启用</label><button class="primary" type="button" :disabled="saving || templateDraft.is_builtin" @click="saveResource('templates', templateDraft, 'template_id')">保存模板</button><button class="danger-button" type="button" :disabled="templateDraft.is_builtin" @click="removeResource('templates', templateDraft, 'template_id')">删除</button></div>
          </div>
          <div class="template-preview" :style="{ '--preview-primary': templateDraft.config.primary_color, '--preview-accent': templateDraft.config.accent_color, '--preview-bg': templateDraft.config.background_color }">
            <div class="template-preview-page"><header><small>{{ templateDraft.config.logo_text }}</small><strong>{{ templateDraft.config.title_prefix }} · 示例报表</strong></header><div class="template-preview-body"><div v-if="templateDraft.config.show_meta" class="template-preview-meta"><span>报告周期<br><b>2026-08</b></span><span>生成时间<br><b>当前时间</b></span></div><div class="template-preview-metrics"><span><small>完成任务</small><b>232</b></span><span><small>切割长度</small><b>9,714</b></span></div><div class="template-preview-content" :class="templateDraft.config.layout"><div class="fake-chart"><i v-for="height in [42,68,54,82,64,76]" :key="height" :style="{ height: `${height}%` }"></i></div><div class="fake-table"><span v-for="row in 6" :key="row"></span></div></div></div></div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>
