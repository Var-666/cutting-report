<script setup lang="ts">
import { computed, shallowRef, useId } from 'vue'
import { NAlert, NButton, NDatePicker, NFormItem, NRadioButton, NRadioGroup, NSelect } from 'naive-ui'
import type { PdmDocumentKind, PdmFilterField, PdmFilterOptions, PdmFilters, PdmOption } from '../../utils/pdmFilters'
import { clonePdmFilters, pdmDocumentKinds, pdmFilterFields, pdmTypeOptions, validatePdmFilters } from '../../utils/pdmFilters'

const filters = defineModel<PdmFilters>({ required: true })
const props = withDefaults(defineProps<{
  options?: PdmFilterOptions
  status?: 'unavailable' | 'loading' | 'ready' | 'error'
  error?: string
  pending?: boolean
  querying?: boolean
}>(), { options: () => ({}), status: 'unavailable', error: '', pending: false, querying: false })
const emit = defineEmits<{ submit: [filters: PdmFilters]; retry: [] }>()
const id = useId()
const validationError = shallowRef('')
const visibleKinds = computed(() => pdmDocumentKinds.filter(item => filters.value.documentKinds.includes(item.value)))
const period = computed<[string, string] | null>(() => filters.value.startDate && filters.value.endDate ? [filters.value.startDate, filters.value.endDate] : null)
const staticFields = new Set<PdmFilterField>(['types', 'manufactory'])

function optionsFor(kind: PdmDocumentKind, field: PdmFilterField): PdmOption[] {
  if (field === 'types') return pdmTypeOptions[kind]
  if (field === 'manufactory') return [{ value: '是', label: '是' }, { value: '否', label: '否' }]
  return props.options[kind]?.[field === 'responsiblePeople' ? 'responsible_people' : field] || []
}
function setKinds(values: PdmDocumentKind[]) {
  filters.value = { ...filters.value, documentKinds: [...values] }
  validationError.value = ''
}
function setField(kind: PdmDocumentKind, field: PdmFilterField, values: string[]) {
  const next = clonePdmFilters(filters.value)
  next[kind][field] = [...values]
  if (field === 'types' || field === 'projects') {
    for (const key of ['professions', 'levels', 'areas', 'manufactory', 'batches', 'classifications', 'weights'] as const) next[kind][key] = []
    next[kind].responsiblePeople = null
  }
  filters.value = next
}
function setPeriod(value: string | [string, string] | null) {
  const dates = Array.isArray(value) ? value : ['', '']
  filters.value = { ...filters.value, startDate: dates[0], endDate: dates[1] }
  validationError.value = ''
}
function submit() {
  if (props.status !== 'ready') return
  validationError.value = validatePdmFilters(filters.value)
  if (!validationError.value) emit('submit', clonePdmFilters(filters.value))
}

</script>

<template>
  <form class="pdm-filters" aria-label="PDM资料筛选" :aria-busy="status === 'loading'" @submit.prevent="submit">
    <div class="pdm-filters__common">
      <NFormItem label="文档类型" :label-for="`${id}-kinds`" :show-feedback="false">
        <NSelect :value="filters.documentKinds" :options="pdmDocumentKinds.slice()" multiple filterable :clearable="false" size="large" :max-tag-count="1" :input-props="{ id: `${id}-kinds`, 'aria-label': '文档类型' }" @update:value="setKinds" />
      </NFormItem>
      <NFormItem label="统计方式" :show-feedback="false">
        <NRadioGroup :value="filters.groupBy" size="large" aria-label="统计方式" @update:value="value => { if (value === 'summary' || value === 'project' || value === 'profession') filters = { ...filters, groupBy: value } }">
          <NRadioButton value="summary">统计所有数据</NRadioButton><NRadioButton value="project">按项目统计</NRadioButton><NRadioButton value="profession">按专业统计</NRadioButton>
        </NRadioGroup>
      </NFormItem>
      <NFormItem class="pdm-filters__period" label="时间段" :show-feedback="false">
        <NDatePicker :formatted-value="period" type="daterange" value-format="yyyy-MM-dd" format="yyyy-MM-dd" size="large" clearable :input-readonly="false" start-placeholder="开始日期" end-placeholder="结束日期" :status="validationError ? 'error' : undefined" @update:formatted-value="setPeriod" />
      </NFormItem>
    </div>

    <section v-for="kind in visibleKinds" :key="kind.value" class="pdm-filters__branch" :aria-labelledby="`${id}-${kind.value}`">
      <h2 :id="`${id}-${kind.value}`">{{ kind.label }}</h2>
      <div class="pdm-filters__fields">
        <NFormItem v-for="field in pdmFilterFields[kind.value]" :key="field.key" :label="field.label" :label-for="`${id}-${kind.value}-${field.key}`" :show-feedback="false">
          <NSelect :value="filters[kind.value][field.key]" :options="optionsFor(kind.value, field.key)" multiple filterable clearable size="large" :max-tag-count="1"
            :disabled="!staticFields.has(field.key) && status !== 'ready'" :loading="!staticFields.has(field.key) && status === 'loading'"
            :placeholder="!staticFields.has(field.key) && status !== 'ready' ? status === 'loading' ? '正在加载' : status === 'error' ? '选项加载失败' : '待接入 PDM' : field.key === 'responsiblePeople' ? '请选择负责人' : '全部'"
            :input-props="{ id: `${id}-${kind.value}-${field.key}`, 'aria-label': `${kind.label}${field.label}` }"
            @update:value="values => setField(kind.value, field.key, values)">
            <template #empty>当前范围暂无可选项</template>
            <template #action><div class="pdm-filters__select-actions"><NButton text size="small" @click="setField(kind.value, field.key, optionsFor(kind.value, field.key).map(item => item.value))">全选</NButton><NButton text size="small" @click="setField(kind.value, field.key, [])">清空</NButton></div></template>
          </NSelect>
        </NFormItem>
      </div>
    </section>

    <NAlert v-if="status === 'unavailable'" type="info" :show-icon="false">PDM 数据源尚未启用，请在数据库配置中启用文件库连接。</NAlert>
    <NAlert v-else-if="status === 'error'" type="error" title="筛选选项加载失败"><span>{{ error }}</span><NButton text @click="emit('retry')">重试</NButton></NAlert>
    <p v-if="validationError" class="pdm-filters__error" role="alert">{{ validationError }}</p>
    <footer class="pdm-filters__footer"><span v-if="pending" role="status">筛选条件已修改，查询后更新结果</span><NButton attr-type="submit" type="primary" size="large" :disabled="status !== 'ready'" :loading="querying">查询</NButton></footer>
  </form>
</template>

<style scoped>
.pdm-filters{display:grid;gap:var(--space-4);padding:var(--space-4);border:1px solid var(--theme-border-color);border-radius:var(--report-panel-radius);background:rgb(var(--container-bg-color));}
.pdm-filters__common{display:grid;grid-template-columns:minmax(200px,1fr) auto minmax(300px,1.4fr);gap:var(--space-4);align-items:start}
.pdm-filters__fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--space-4)}
.pdm-filters__branch{min-width:0;padding-top:var(--space-4);border-top:1px solid var(--theme-divider-color)}
.pdm-filters__branch h2{margin:0 0 var(--space-3);font-size:var(--font-section-title);color:var(--text-color-1);font-weight:600}
.pdm-filters :deep(.n-form-item),.pdm-filters :deep(.n-form-item-blank){min-width:0}
.pdm-filters :deep(.n-form-item-label){font-size:var(--font-control);color:var(--text-color-2)}
.pdm-filters :deep(.n-select),.pdm-filters :deep(.n-date-picker){width:100%;min-width:0}
.pdm-filters__select-actions{display:flex;justify-content:space-between;gap:var(--space-3)}
.pdm-filters__footer{display:flex;justify-content:flex-end;align-items:center;gap:var(--space-4)}
.pdm-filters__footer>span{margin-right:auto;color:rgb(var(--warning-color));font-size:var(--font-control)}
.pdm-filters__error{margin:0;color:rgb(var(--error-color));font-size:var(--font-control)}
</style>
