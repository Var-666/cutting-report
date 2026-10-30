<script setup lang="ts">
import { useDesignerQueryPreview } from '../composables/useDesignerQueryPreview'
import type { DesignerDefinition, DesignerResource, DesignerResources, QueryTask, ReportTemplate } from '../types/designer'
import { toError } from '../utils/errors'
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deleteDesignerResource, listDesignerResources, saveDesignerResource } from '../api/designer.ts'
import DesignerQueryEditor from '../components/designer/DesignerQueryEditor.vue'
import DesignerDefinitionEditor from '../components/designer/DesignerDefinitionEditor.vue'
import DesignerTemplateEditor from '../components/designer/DesignerTemplateEditor.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import { useUnsavedChanges } from '../composables/useUnsavedChanges.ts'

const emit = defineEmits<{ 'definitions-change': [] }>()
const tabs = [
  { id: 'queries', label: '查询任务' },
  { id: 'definitions', label: '报表定义' },
  { id: 'templates', label: '模板设计器' },
]
const route = useRoute()
const router = useRouter()
const activeTab = ref(tabs.some((item) => item.id === route.query.tab) ? route.query.tab : 'queries')
const hasUnsavedChanges = ref(false)
const adminToken = ref('')
const queryTasks = ref<QueryTask[]>([])
const templates = ref<ReportTemplate[]>([])
const definitions = ref<DesignerDefinition[]>([])
const queryDraft = ref<QueryTask | null>(null)
const templateDraft = ref<ReportTemplate | null>(null)
const definitionDraft = ref<DesignerDefinition | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
const { queryValidation, queryPreview, validateQuery, previewQuery } = useDesignerQueryPreview({ queryDraft, adminToken, today, error, notice })
useUnsavedChanges(hasUnsavedChanges, '报表设计中还有未保存的修改，确定离开吗？')

watch([queryDraft, templateDraft, definitionDraft], () => {
  hasUnsavedChanges.value = true
}, { deep: true })
watch(activeTab, (tab) => {
  if (route.query.tab === tab) return
  router.replace({ query: { ...route.query, tab } })
})
watch(() => route.query.tab, (tab) => {
  if (tabs.some((item) => item.id === tab)) activeTab.value = tab
})

function selectTab(tab: string) {
  activeTab.value = tab
}

function moveTab(event: KeyboardEvent, index: number) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const nextIndex = event.key === 'Home' ? 0
    : event.key === 'End' ? tabs.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
  activeTab.value = tabs[nextIndex].id
  nextTick(() => document.getElementById(`designer-tab-${tabs[nextIndex].id}`)?.focus())
}

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))

function blankQuery(): QueryTask {
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

function blankTemplate(source: ReportTemplate | null = null): ReportTemplate {
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

function blankDefinition(): DesignerDefinition {
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
    await nextTick()
    hasUnsavedChanges.value = false
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

function mayReplaceDraft() { return !hasUnsavedChanges.value || window.confirm('当前设计有未保存的修改，确定放弃吗？') }
function chooseQuery(item: QueryTask) { if (!mayReplaceDraft()) return; queryDraft.value = clone(item); queryValidation.value = null; queryPreview.value = null; error.value = ''; notice.value = ''; nextTick(() => { hasUnsavedChanges.value = false }) }
function chooseTemplate(item: ReportTemplate) { if (!mayReplaceDraft()) return; templateDraft.value = clone(item); error.value = ''; notice.value = ''; nextTick(() => { hasUnsavedChanges.value = false }) }
function chooseDefinition(item: DesignerDefinition) { if (!mayReplaceDraft()) return; definitionDraft.value = clone(item); error.value = ''; notice.value = ''; nextTick(() => { hasUnsavedChanges.value = false }) }

async function saveResource(resource: keyof DesignerResources, draft: DesignerResource, idKey: string) {
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const id = String(Reflect.get(draft, idKey) || '')
    const saved = await saveDesignerResource(resource, draft, id, adminToken.value)
    if (resource === 'query-tasks' && 'sql_text' in saved) queryDraft.value = clone(saved)
    if (resource === 'templates' && 'config' in saved) templateDraft.value = clone(saved)
    if (resource === 'definitions' && 'visual_config' in saved) definitionDraft.value = clone(saved)
    notice.value = '保存成功，配置版本已更新'
    await loadResources()
    hasUnsavedChanges.value = false
    if (resource === 'definitions') emit('definitions-change')
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message } finally { saving.value = false }
}

async function removeResource(resource: keyof DesignerResources, draft: DesignerResource, idKey: string) {
  const id = String(Reflect.get(draft, idKey) || '')
  if (!id || draft.is_builtin || !window.confirm(`确认删除“${draft.name}”？`)) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    await deleteDesignerResource(resource, id, adminToken.value)
    notice.value = '删除成功'
    if (resource === 'query-tasks') queryDraft.value = blankQuery()
    if (resource === 'templates') templateDraft.value = blankTemplate()
    if (resource === 'definitions') { definitionDraft.value = blankDefinition(); emit('definitions-change') }
    await loadResources()
    hasUnsavedChanges.value = false
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message } finally { saving.value = false }
}

onMounted(loadResources)
</script>

<template>
  <div class="management-page designer-page">
  <ManagementPageHero
    eyebrow="REPORT DESIGN STUDIO"
    title="报表定义与查询任务管理"
    description="将只读 SQL、字段指标、图表和离线 HTML 模板组合为可发布的新报表。"
    icon="grid"
    status-label="设计流程"
    status-value="查询 · 定义 · 模板"
    status-note="配置驱动，发布后进入统一报表生成管线"
    tone="violet"
  />
  <section class="designer-access-strip">
    <div><strong>管理访问</strong><span>SQL Server 模式下，加载与保存设计资源需要管理员令牌。</span></div>
    <label class="designer-token"><span>管理员令牌</span><input v-model="adminToken" name="designer_admin_token" type="password" autocomplete="current-password" placeholder="SQL Server 模式需填写…" @change="loadResources" /></label>
  </section>

  <section class="panel designer-shell">
    <div class="designer-tabs" role="tablist">
      <button v-for="(tab, index) in tabs" :id="`designer-tab-${tab.id}`" :key="tab.id" type="button" role="tab" :aria-selected="activeTab === tab.id" :aria-controls="`designer-panel-${tab.id}`" :tabindex="activeTab === tab.id ? 0 : -1" :class="{ active: activeTab === tab.id }" @click="selectTab(tab.id)" @keydown="moveTab($event, index)">{{ tab.label }}</button>
    </div>
    <div v-if="error" class="designer-message error" role="alert">{{ error }}</div>
    <div v-if="notice" class="designer-message success" role="status" aria-live="polite">{{ notice }}</div>

    <div v-if="activeTab === 'queries'" id="designer-panel-queries" class="designer-workspace" role="tabpanel" aria-labelledby="designer-tab-queries">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>查询任务</strong><button type="button" @click="queryDraft = blankQuery()">新建</button></div>
        <button v-for="item in queryTasks" :key="item.query_task_id" type="button" class="designer-list-item" :class="{ active: queryDraft?.query_task_id === item.query_task_id }" @click="chooseQuery(item)">
          <strong>{{ item.name }}</strong><span>{{ item.code }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i>
        </button>
        <p v-if="!queryTasks.length && !loading">暂无查询任务</p>
      </aside>
      <DesignerQueryEditor v-model="queryDraft" :saving="saving" :query-preview="queryPreview" @validate="validateQuery" @preview="previewQuery" @save="saveResource('query-tasks', $event, 'query_task_id')" @remove="removeResource('query-tasks', $event, 'query_task_id')" />
    </div>

    <div v-else-if="activeTab === 'definitions'" id="designer-panel-definitions" class="designer-workspace" role="tabpanel" aria-labelledby="designer-tab-definitions">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>报表定义</strong><button type="button" @click="definitionDraft = blankDefinition()">新建</button></div>
        <button v-for="item in definitions" :key="item.definition_id" type="button" class="designer-list-item" :class="{ active: definitionDraft?.definition_id === item.definition_id }" @click="chooseDefinition(item)"><strong>{{ item.name }}</strong><span>{{ item.is_builtin ? '内置报表' : item.query_task_name }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i></button>
      </aside>
      <DesignerDefinitionEditor v-model="definitionDraft" :saving="saving" :query-tasks="queryTasks" :templates="templates" @save="saveResource('definitions', $event, 'definition_id')" @remove="removeResource('definitions', $event, 'definition_id')" />
    </div>

    <div v-else id="designer-panel-templates" class="designer-workspace template-workspace" role="tabpanel" aria-labelledby="designer-tab-templates">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>报表模板</strong><button type="button" @click="templateDraft = blankTemplate()">新建</button></div>
        <button v-for="item in templates" :key="item.template_id" type="button" class="designer-list-item" :class="{ active: templateDraft?.template_id === item.template_id }" @click="chooseTemplate(item)"><strong>{{ item.name }}</strong><span>{{ item.code }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i></button>
      </aside>
      <DesignerTemplateEditor v-model="templateDraft" :saving="saving" @copy="templateDraft = blankTemplate($event)" @save="saveResource('templates', $event, 'template_id')" @remove="removeResource('templates', $event, 'template_id')" />
    </div>
  </section>
  </div>
</template>
