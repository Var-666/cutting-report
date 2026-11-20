<script setup lang="ts">
import { useDesignerQueryPreview } from '../composables/useDesignerQueryPreview'
import type { DesignerDefinition, DesignerResource, DesignerResources, QueryTask, ReportTemplate } from '../types/designer'
import { toError } from '../utils/errors'
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deleteDesignerResource, listDesignerResources, saveDesignerResource } from '../api/designer.ts'
import DesignerQueryEditor from '../components/designer/DesignerQueryEditor.vue'
import DesignerDefinitionEditor from '../components/designer/DesignerDefinitionEditor.vue'
import DesignerTemplateEditor from '../components/designer/DesignerTemplateEditor.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'
import { useConfirm } from '../composables/useConfirm'
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
const drafts = { 'query-tasks': queryDraft, templates: templateDraft, definitions: definitionDraft }
const savedDrafts = reactive({ 'query-tasks': 'null', templates: 'null', definitions: 'null' })
const resourceLabels = { 'query-tasks': '查询任务', templates: '报表模板', definitions: '报表定义' }
function isDirty(resource: keyof DesignerResources) {
  return JSON.stringify(drafts[resource].value) !== savedDrafts[resource]
}
function markSaved(resource: keyof DesignerResources) {
  savedDrafts[resource] = JSON.stringify(drafts[resource].value)
}
const hasUnsavedChanges = computed(() => isDirty('query-tasks') || isDirty('templates') || isDirty('definitions'))
const canDiscard = useUnsavedChanges(hasUnsavedChanges, '离开将丢弃尚未保存的报表设计。', saving)
const confirm = useConfirm()
let disposed = false
onBeforeUnmount(() => { disposed = true })

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
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const [queries, templateData, definitionData] = await Promise.all([
      listDesignerResources('query-tasks', adminToken.value),
      listDesignerResources('templates', adminToken.value),
      listDesignerResources('definitions', adminToken.value),
    ])
    if (disposed) return
    queryTasks.value = queries.items
    templates.value = templateData.items
    definitions.value = definitionData.items
    if (!queryDraft.value) { queryDraft.value = blankQuery(); markSaved('query-tasks') }
    if (!templateDraft.value) { templateDraft.value = blankTemplate(); markSaved('templates') }
    if (!definitionDraft.value) { definitionDraft.value = blankDefinition(); markSaved('definitions') }
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  } finally {
    loading.value = false
  }
}

async function mayReplaceDraft(resource: keyof DesignerResources) {
  return await canDiscard(`替换当前${resourceLabels[resource]}将丢弃该草稿的修改。`, isDirty(resource))
}
async function chooseQuery(item?: QueryTask) {
  if (!await mayReplaceDraft('query-tasks') || disposed || saving.value) return
  queryDraft.value = item ? clone(item) : blankQuery()
  markSaved('query-tasks')
  queryValidation.value = null
  queryPreview.value = null
  error.value = ''
  notice.value = ''
}
async function chooseTemplate(item?: ReportTemplate, copy = false) {
  if (!await mayReplaceDraft('templates') || disposed || saving.value) return
  templateDraft.value = item && !copy ? clone(item) : blankTemplate(item ?? null)
  if (copy) savedDrafts.templates = JSON.stringify(blankTemplate())
  else markSaved('templates')
  error.value = ''
  notice.value = ''
}
async function chooseDefinition(item?: DesignerDefinition) {
  if (!await mayReplaceDraft('definitions') || disposed || saving.value) return
  definitionDraft.value = item ? clone(item) : blankDefinition()
  markSaved('definitions')
  error.value = ''
  notice.value = ''
}

async function saveResource(resource: keyof DesignerResources, draft: DesignerResource, idKey: string) {
  if (saving.value || loading.value) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    const id = String(Reflect.get(draft, idKey) || '')
    const saved = await saveDesignerResource(resource, draft, id, adminToken.value)
    if (disposed) return
    if (resource === 'query-tasks' && 'sql_text' in saved) queryDraft.value = clone(saved)
    if (resource === 'templates' && 'config' in saved) templateDraft.value = clone(saved)
    if (resource === 'definitions' && 'visual_config' in saved) definitionDraft.value = clone(saved)
    notice.value = '保存成功，配置版本已更新'
    markSaved(resource)
    await loadResources()
    if (resource === 'definitions') emit('definitions-change')
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message } finally { saving.value = false }
}

async function removeResource(resource: keyof DesignerResources, draft: DesignerResource, idKey: string) {
  const id = String(Reflect.get(draft, idKey) || '')
  if (!id || draft.is_builtin || loading.value || saving.value) return
  const name = JSON.parse(savedDrafts[resource])?.name || draft.name
  if (!await confirm({
    title: `删除${resourceLabels[resource]}？`,
    content: `将删除“${name}”。仍被其他设计引用的资源受系统限制。${isDirty(resource) ? '删除成功后，当前草稿也会丢弃。' : ''}`,
    positiveText: `删除${resourceLabels[resource]}`, type: 'error',
  }) || disposed || saving.value || drafts[resource].value !== draft) return
  saving.value = true; error.value = ''; notice.value = ''
  try {
    await deleteDesignerResource(resource, id, adminToken.value)
    if (disposed) return
    notice.value = '删除成功'
    if (resource === 'query-tasks') queryDraft.value = blankQuery()
    if (resource === 'templates') templateDraft.value = blankTemplate()
    if (resource === 'definitions') { definitionDraft.value = blankDefinition(); emit('definitions-change') }
    markSaved(resource)
    await loadResources()
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message } finally { saving.value = false }
}

onMounted(loadResources)
</script>

<template>
  <div class="management-page designer-page">
  <ManagementPageHero title="报表定义与查询任务管理" />
  <section class="designer-access-strip">
    <div><strong>管理访问</strong><span>SQL Server 模式下，加载与保存设计资源需要管理员令牌。</span></div>
    <label class="designer-token"><span>管理员令牌</span><input v-model="adminToken" name="designer_admin_token" type="password" autocomplete="current-password" placeholder="SQL Server 模式需填写…" :disabled="saving || loading" @change="loadResources" /></label>
  </section>

  <section class="panel designer-shell" :inert="saving" :aria-busy="saving">
    <div class="designer-tabs" role="tablist">
      <button v-for="(tab, index) in tabs" :id="`designer-tab-${tab.id}`" :key="tab.id" type="button" role="tab" :aria-selected="activeTab === tab.id" :aria-controls="`designer-panel-${tab.id}`" :tabindex="activeTab === tab.id ? 0 : -1" :class="{ active: activeTab === tab.id }" @click="selectTab(tab.id)" @keydown="moveTab($event, index)">{{ tab.label }}</button>
    </div>
    <div v-if="error" class="designer-message error" role="alert">{{ error }}</div>
    <div v-if="notice" class="designer-message success" role="status" aria-live="polite">{{ notice }}</div>

    <div v-if="activeTab === 'queries'" id="designer-panel-queries" class="designer-workspace" role="tabpanel" aria-labelledby="designer-tab-queries">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>查询任务</strong><button type="button" @click="chooseQuery()">新建</button></div>
        <button v-for="item in queryTasks" :key="item.query_task_id" type="button" class="designer-list-item" :class="{ active: queryDraft?.query_task_id === item.query_task_id }" @click="chooseQuery(item)">
          <strong>{{ item.name }}</strong><span>{{ item.code }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i>
        </button>
        <p v-if="!queryTasks.length && !loading">暂无查询任务</p>
      </aside>
      <DesignerQueryEditor v-model="queryDraft" :saving="saving" :query-preview="queryPreview" @validate="validateQuery" @preview="previewQuery" @save="saveResource('query-tasks', $event, 'query_task_id')" @remove="removeResource('query-tasks', $event, 'query_task_id')" />
    </div>

    <div v-else-if="activeTab === 'definitions'" id="designer-panel-definitions" class="designer-workspace" role="tabpanel" aria-labelledby="designer-tab-definitions">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>报表定义</strong><button type="button" @click="chooseDefinition()">新建</button></div>
        <button v-for="item in definitions" :key="item.definition_id" type="button" class="designer-list-item" :class="{ active: definitionDraft?.definition_id === item.definition_id }" @click="chooseDefinition(item)"><strong>{{ item.name }}</strong><span>{{ item.is_builtin ? '内置报表' : item.query_task_name }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i></button>
      </aside>
      <DesignerDefinitionEditor v-model="definitionDraft" :saving="saving" :query-tasks="queryTasks" :templates="templates" @save="saveResource('definitions', $event, 'definition_id')" @remove="removeResource('definitions', $event, 'definition_id')" />
    </div>

    <div v-else id="designer-panel-templates" class="designer-workspace template-workspace" role="tabpanel" aria-labelledby="designer-tab-templates">
      <aside class="designer-list">
        <div class="designer-list-head"><strong>报表模板</strong><button type="button" @click="chooseTemplate()">新建</button></div>
        <button v-for="item in templates" :key="item.template_id" type="button" class="designer-list-item" :class="{ active: templateDraft?.template_id === item.template_id }" @click="chooseTemplate(item)"><strong>{{ item.name }}</strong><span>{{ item.code }} · V{{ item.version }}</span><i :class="{ enabled: item.enabled }"></i></button>
      </aside>
      <DesignerTemplateEditor v-model="templateDraft" :saving="saving" @copy="chooseTemplate($event, true)" @save="saveResource('templates', $event, 'template_id')" @remove="removeResource('templates', $event, 'template_id')" />
    </div>
  </section>
  </div>
</template>
