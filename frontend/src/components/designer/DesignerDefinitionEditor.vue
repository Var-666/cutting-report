<script setup lang="ts">
import type { QueryTask, DesignerDefinition, ReportTemplate } from '../../types/designer'
import { computed } from 'vue'


const definitionDraft = defineModel<DesignerDefinition | null>({ required: true })
const props = defineProps<{ saving: boolean; queryTasks: QueryTask[]; templates: ReportTemplate[] }>()
const emit = defineEmits<{ save: [draft: DesignerDefinition]; remove: [draft: DesignerDefinition] }>()

const selectedDefinitionQuery = computed(() => props.queryTasks.find((item) => item.query_task_id === definitionDraft.value?.query_task_id))
const definitionFields = computed(() => selectedDefinitionQuery.value?.columns || [])
function addMetric() {
  if (!definitionDraft.value) return
  const field = definitionFields.value.find((item) => item.type === 'number') || definitionFields.value[0]
  if (field) definitionDraft.value.visual_config.metrics.push({ field: field.field, label: field.label, aggregation: 'sum', unit: field.unit })
}
function removeMetric(index: number) {
  if (!definitionDraft.value) return
  definitionDraft.value.visual_config.metrics.splice(index, 1) }
function toggleColumn(field: string, checked: boolean) {
  if (!definitionDraft.value) return
  const columns = definitionDraft.value.visual_config.columns
  if (checked && !columns.includes(field)) columns.push(field)
  if (!checked) definitionDraft.value.visual_config.columns = columns.filter((item) => item !== field)
}
function toggleSeries(field: string, checked: boolean) {
  if (!definitionDraft.value) return
  const fields = definitionDraft.value.visual_config.chart.value_fields
  if (checked && !fields.includes(field)) fields.push(field)
  if (!checked) definitionDraft.value.visual_config.chart.value_fields = fields.filter((item) => item !== field)
}
function syncDefinitionFields() {
  if (!definitionDraft.value) return
  const fields = definitionFields.value
  definitionDraft.value.visual_config.columns = fields.map((item) => item.field)
  definitionDraft.value.visual_config.metrics = []
  definitionDraft.value.visual_config.chart.category_field = fields[0]?.field || ''
  definitionDraft.value.visual_config.chart.value_fields = fields.filter((item) => item.type === 'number').slice(0, 2).map((item) => item.field)
}
</script>

<template>
  <div v-if="definitionDraft" class="designer-editor">
        <div class="designer-editor-head"><div><span>REPORT DEFINITION</span><h3>{{ definitionDraft.definition_id ? definitionDraft.name : '新建报表定义' }}</h3></div><small v-if="definitionDraft.is_builtin">系统内置定义保持现有专用管线</small></div>
        <div class="designer-form-grid">
          <label><span>报表名称</span><input v-model="definitionDraft.name" name="definition_name" autocomplete="off" :disabled="definitionDraft.is_builtin" /></label>
          <label><span>定义编码</span><input v-model="definitionDraft.code" name="definition_code" autocomplete="off" spellcheck="false" :disabled="definitionDraft.is_builtin || !!definitionDraft.definition_id" placeholder="例如：quality_daily…" /></label>
          <label><span>查询任务</span><AppSelect v-model="definitionDraft.query_task_id" :disabled="definitionDraft.is_builtin" @change="syncDefinitionFields"><option value="">请选择</option><option v-for="item in queryTasks.filter(row => row.enabled && !row.is_builtin)" :key="item.query_task_id" :value="item.query_task_id">{{ item.name }}</option></AppSelect></label>
          <label><span>报表模板</span><AppSelect v-model="definitionDraft.template_id" :disabled="definitionDraft.is_builtin"><option v-for="item in templates.filter(row => row.enabled)" :key="item.template_id" :value="item.template_id">{{ item.name }}</option></AppSelect></label>
          <label><span>周期类型</span><AppSelect v-model="definitionDraft.period_type" :disabled="definitionDraft.is_builtin"><option value="day">按日</option><option value="month">按月</option></AppSelect></label>
          <label class="wide"><span>说明</span><input v-model="definitionDraft.description" name="definition_description" autocomplete="off" :disabled="definitionDraft.is_builtin" /></label>
        </div>
        <template v-if="!definitionDraft.is_builtin">
          <div class="designer-section-title"><strong>明细表字段</strong><span>选择导出表格需要显示的列</span></div>
          <div class="designer-check-grid"><label v-for="field in definitionFields" :key="field.field"><input :name="`definition_column_${field.field}`" type="checkbox" :checked="definitionDraft.visual_config.columns.includes(field.field)" @change="toggleColumn(field.field, ($event.target as HTMLInputElement).checked)" />{{ field.label }}</label></div>
          <div class="designer-section-title"><strong>核心指标</strong><button type="button" @click="addMetric">增加指标</button></div>
          <div class="designer-fields"><div v-for="(metric, index) in definitionDraft.visual_config.metrics" :key="index" class="designer-field-row metric-row"><AppSelect v-model="metric.field" :aria-label="`核心指标 ${index + 1} 字段`"><option v-for="field in definitionFields" :key="field.field" :value="field.field">{{ field.label }}</option></AppSelect><input v-model="metric.label" :name="`metric_label_${index}`" :aria-label="`核心指标 ${index + 1} 名称`" autocomplete="off" placeholder="例如：总切割长度…" /><AppSelect v-model="metric.aggregation" :aria-label="`核心指标 ${index + 1} 聚合方式`"><option value="sum">求和</option><option value="avg">平均</option><option value="count">计数</option><option value="max">最大值</option><option value="min">最小值</option></AppSelect><input v-model="metric.unit" :name="`metric_unit_${index}`" :aria-label="`核心指标 ${index + 1} 单位`" autocomplete="off" placeholder="例如：米…" /><button type="button" @click="removeMetric(index)">删除</button></div></div>
          <div class="designer-section-title"><strong>图表配置</strong></div>
          <div class="designer-form-grid">
            <label><span>图表标题</span><input v-model="definitionDraft.visual_config.chart.title" name="chart_title" autocomplete="off" /></label>
            <label><span>图表类型</span><AppSelect v-model="definitionDraft.visual_config.chart.type"><option value="bar">柱状图</option><option value="line">折线图</option><option value="area">面积图</option><option value="bar_line">柱线组合</option></AppSelect></label>
            <label><span>分类轴字段</span><AppSelect v-model="definitionDraft.visual_config.chart.category_field"><option v-for="field in definitionFields" :key="field.field" :value="field.field">{{ field.label }}</option></AppSelect></label>
          </div>
          <div class="designer-check-grid"><label v-for="field in definitionFields.filter(item => item.type === 'number')" :key="field.field"><input :name="`chart_series_${field.field}`" type="checkbox" :checked="definitionDraft.visual_config.chart.value_fields.includes(field.field)" @change="toggleSeries(field.field, ($event.target as HTMLInputElement).checked)" />{{ field.label }}</label></div>
        </template>
        <div class="designer-actions"><label class="switch-label"><input v-model="definitionDraft.enabled" name="definition_enabled" type="checkbox" :disabled="definitionDraft.is_builtin" />发布并启用</label><button class="primary" type="button" :disabled="saving || definitionDraft.is_builtin" @click="emit('save', definitionDraft)">保存定义</button><button class="danger-button" type="button" :disabled="definitionDraft.is_builtin" @click="emit('remove', definitionDraft)">删除</button></div>
      </div>
</template>
