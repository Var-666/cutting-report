<script setup lang="ts">
import type { QueryTask, QueryPreview } from '../../types/designer'
import DesignerQueryPreview from './DesignerQueryPreview.vue'

const queryDraft = defineModel<QueryTask | null>({ required: true })
defineProps<{ saving: boolean; queryPreview: QueryPreview | null }>()
const emit = defineEmits<{ save: [draft: QueryTask]; remove: [draft: QueryTask]; validate: []; preview: [] }>()

function addColumn() { if (!queryDraft.value) return; queryDraft.value.columns.push({ field: '', label: '', type: 'text', unit: '' }) }
function removeColumn(index: number) { if (!queryDraft.value) return; queryDraft.value.columns.splice(index, 1) }
</script>

<template>
  <div v-if="queryDraft" class="designer-editor">
        <div class="designer-editor-head"><div><span>QUERY TASK</span><h3>{{ queryDraft.query_task_id ? queryDraft.name : '新建查询任务' }}</h3></div><small v-if="queryDraft.is_builtin">系统内置，只读</small></div>
        <div class="designer-form-grid">
          <label><span>任务名称</span><input v-model="queryDraft.name" name="query_name" autocomplete="off" :disabled="queryDraft.is_builtin" /></label>
          <label><span>任务编码</span><input v-model="queryDraft.code" name="query_code" autocomplete="off" spellcheck="false" :disabled="queryDraft.is_builtin || !!queryDraft.query_task_id" placeholder="例如：daily_quality…" /></label>
          <label class="wide"><span>说明</span><input v-model="queryDraft.description" name="query_description" autocomplete="off" :disabled="queryDraft.is_builtin" /></label>
        </div>
        <label class="designer-block"><span>只读 T-SQL</span><textarea v-model="queryDraft.sql_text" name="query_sql" class="designer-sql" autocomplete="off" spellcheck="false" :disabled="queryDraft.is_builtin"></textarea><small>支持 :period_start、:period_end、:report_date、:project_code、:ship_no 参数，执行时使用参数化查询。</small></label>
        <div class="designer-section-title"><strong>返回字段</strong><button type="button" :disabled="queryDraft.is_builtin" @click="addColumn">增加字段</button></div>
        <div class="designer-fields">
          <div v-for="(column, index) in queryDraft.columns" :key="index" class="designer-field-row">
            <input v-model="column.field" :name="`query_column_field_${index}`" :aria-label="`返回字段 ${index + 1} 字段名`" autocomplete="off" spellcheck="false" placeholder="例如：field_name…" :disabled="queryDraft.is_builtin" />
            <input v-model="column.label" :name="`query_column_label_${index}`" :aria-label="`返回字段 ${index + 1} 显示名称`" autocomplete="off" placeholder="例如：切割长度…" :disabled="queryDraft.is_builtin" />
            <AppSelect v-model="column.type" :aria-label="`返回字段 ${index + 1} 类型`" :disabled="queryDraft.is_builtin"><option value="text">文本</option><option value="number">数值</option><option value="date">日期</option><option value="datetime">时间</option><option value="duration">时长</option></AppSelect>
            <input v-model="column.unit" :name="`query_column_unit_${index}`" :aria-label="`返回字段 ${index + 1} 单位`" autocomplete="off" placeholder="例如：米…" :disabled="queryDraft.is_builtin" />
            <button type="button" :disabled="queryDraft.is_builtin" @click="removeColumn(index)">删除</button>
          </div>
        </div>
        <div class="designer-actions">
          <label class="switch-label"><input v-model="queryDraft.enabled" name="query_enabled" type="checkbox" :disabled="queryDraft.is_builtin" />启用</label>
          <button type="button" :disabled="queryDraft.is_builtin" @click="emit('validate')">校验 SQL</button>
          <button type="button" :disabled="!queryDraft.query_task_id" @click="emit('preview')">预览数据</button>
          <button class="primary" type="button" :disabled="saving || queryDraft.is_builtin" @click="emit('save', queryDraft)">保存任务</button>
          <button class="danger-button" type="button" :disabled="queryDraft.is_builtin" @click="emit('remove', queryDraft)">删除</button>
        </div>
        <DesignerQueryPreview v-if="queryPreview" :query-preview="queryPreview" />
      </div>
</template>
