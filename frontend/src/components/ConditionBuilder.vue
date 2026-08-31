<script setup>
import ConditionListInput from './ConditionListInput.vue'
const props = defineProps({
  modelValue: { type: Object, required: true },
  schema: { type: Object, default: null },
  idPrefix: { type: String, required: true },
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue'])
const fieldFor = (rule) => props.schema?.fields.find((field) => field.key === rule.field)
const listOperator = (operator) => ['in', 'not_in'].includes(operator)
const emptyOperator = (operator) => ['is_empty', 'not_empty'].includes(operator)
const inputType = (field) => field?.type === 'number' ? 'number' : field?.type === 'date' ? 'date' : 'text'

function replaceRules(rules) { emit('update:modelValue', { ...props.modelValue, rules }) }
function patchRule(index, patch) { replaceRules(props.modelValue.rules.map((rule, position) => position === index ? { ...rule, ...patch } : rule)) }
function defaultValue(operator) { return emptyOperator(operator) ? null : operator === 'between' ? ['', ''] : listOperator(operator) ? [] : '' }
function addRule() {
  const field = props.schema?.fields[0]
  if (!field || props.modelValue.rules.length >= props.schema.max_rules) return
  const operator = field.operators[0].value
  replaceRules([...props.modelValue.rules, { field: field.key, operator, value: defaultValue(operator) }])
}
function changeField(index, key) {
  const field = props.schema.fields.find((item) => item.key === key)
  const operator = field.operators[0].value
  patchRule(index, { field: key, operator, value: defaultValue(operator) })
}
function changeOperator(index, operator) { patchRule(index, { operator, value: defaultValue(operator) }) }
function updateBound(index, bound, value) {
  const values = [...props.modelValue.rules[index].value]
  values[bound] = value
  patchRule(index, { value: values })
}
function updateChoices(index, event) { patchRule(index, { value: Array.from(event.target.selectedOptions).map((option) => option.value) }) }
</script>

<template>
  <fieldset class="condition-builder" :disabled="disabled || !schema" :aria-label="`${schema?.label || ''}动态检索条件`">
    <legend>组合检索条件</legend>
    <div v-if="schema" class="condition-header">
      <label :for="`${idPrefix}-logic`">条件关系</label>
      <select :id="`${idPrefix}-logic`" :value="modelValue.logic" @change="emit('update:modelValue', { ...modelValue, logic: $event.target.value })"><option v-for="option in schema.logic_options" :key="option.value" :value="option.value">{{ option.label }}</option></select>
      <span>{{ modelValue.rules.length }} / {{ schema.max_rules }} 条</span>
      <button type="button" :disabled="modelValue.rules.length >= schema.max_rules" @click="addRule">＋ 添加条件</button>
      <button type="button" :disabled="!modelValue.rules.length" @click="replaceRules([])">清空条件</button>
    </div>
    <p v-if="!schema" class="condition-empty">正在加载可查询字段…</p>
    <p v-else-if="!modelValue.rules.length" class="condition-empty">{{ schema.requires_filter ? '请添加至少一条条件后查询，不会自动加载全部文件。' : '未添加条件时，分页显示本地记录。' }}</p>
    <div v-for="(rule, index) in modelValue.rules" :key="index" class="condition-row">
      <span class="condition-index">{{ index + 1 }}</span>
      <label><span>字段</span><select :value="rule.field" :aria-label="`${schema.label}条件${index + 1}字段`" required @change="changeField(index, $event.target.value)"><option v-for="field in schema.fields" :key="field.key" :value="field.key">{{ field.label }}</option></select></label>
      <label><span>比较方式</span><select :value="rule.operator" :aria-label="`${schema.label}条件${index + 1}比较方式`" required @change="changeOperator(index, $event.target.value)"><option v-for="operator in fieldFor(rule)?.operators || []" :key="operator.value" :value="operator.value">{{ operator.label }}</option></select></label>
      <div class="condition-value">
        <span v-if="emptyOperator(rule.operator)" class="condition-no-value">不需要填写条件值</span>
        <template v-else-if="rule.operator === 'between'">
          <label><span>起始值</span><input :type="inputType(fieldFor(rule))" :value="rule.value[0]" step="any" required :aria-label="`${schema.label}条件${index + 1}起始值`" @input="updateBound(index, 0, $event.target.value)" /></label>
          <label><span>结束值</span><input :type="inputType(fieldFor(rule))" :value="rule.value[1]" step="any" required :aria-label="`${schema.label}条件${index + 1}结束值`" @input="updateBound(index, 1, $event.target.value)" /></label>
        </template>
        <label v-else-if="fieldFor(rule)?.type === 'enum'"><span>{{ listOperator(rule.operator) ? '可多选（Ctrl / ⌘）' : '条件值' }}</span><select :multiple="listOperator(rule.operator)" :value="rule.value" required :aria-label="`${schema.label}条件${index + 1}值`" @change="listOperator(rule.operator) ? updateChoices(index, $event) : patchRule(index, { value: $event.target.value })"><option v-if="!listOperator(rule.operator)" value="" disabled>请选择</option><option v-for="option in fieldFor(rule).options" :key="option.value" :value="option.value">{{ option.label }}</option></select></label>
        <label v-else-if="listOperator(rule.operator)"><span>多个值（每行一个，或逗号分隔）</span><ConditionListInput :model-value="rule.value" :label="`${schema.label}条件${index + 1}值`" @update:model-value="patchRule(index, { value: $event })" /></label>
        <label v-else><span>条件值</span><input :type="inputType(fieldFor(rule))" :value="rule.value" step="any" maxlength="500" required :aria-label="`${schema.label}条件${index + 1}值`" @input="patchRule(index, { value: $event.target.value })" /></label>
      </div>
      <button type="button" :aria-label="`删除${schema.label}条件${index + 1}`" @click="replaceRules(modelValue.rules.filter((_, position) => position !== index))">删除</button>
      <small v-if="fieldFor(rule)?.hint" class="condition-hint">{{ fieldFor(rule).hint }}</small>
    </div>
    <small v-if="schema" class="condition-note">条件数量可增减，最多 {{ schema.max_rules }} 条；多个值最多 {{ schema.max_values }} 个。日期区间含起止日期，文件大小按字节填写。</small>
  </fieldset>
</template>

<style scoped>
.condition-builder { min-width:0; margin:0; padding:16px; border:1px solid #d5e0e7; background:#f8fbfd; }
.condition-builder legend { color:var(--navy); padding:0 7px; font-size:12px; font-weight:700; }
.condition-header { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:14px; font-size:11px; color:var(--muted); }
.condition-header select { max-width:210px; }
.condition-header button:first-of-type { margin-left:auto; }
.condition-builder button { min-height:32px; padding:6px 12px; font-size:11px; color:var(--blue); background:white; border:1px solid #bbceda; cursor:pointer; }
.condition-builder button:disabled { opacity:.5; cursor:not-allowed; }
.condition-row { display:grid; grid-template-columns:22px minmax(120px,.85fr) minmax(115px,.85fr) minmax(180px,2fr) auto; gap:10px; align-items:end; padding:12px 0; border-top:1px solid #e1e9ef; }
.condition-index { align-self:center; color:#79909f; font-size:11px; }
.condition-row label { display:flex; flex-direction:column; min-width:0; gap:5px; }
.condition-row label > span { color:var(--muted); font-size:10px; }
.condition-builder select,.condition-builder input,.condition-builder textarea { width:100%; min-height:34px; padding:7px 9px; font-size:12px; min-width:0; background:white; }
.condition-header select { width:auto; }
.condition-value { min-width:0; display:flex; gap:9px; align-items:end; }
.condition-value label { flex:1; }
.condition-no-value,.condition-empty { color:var(--muted); font-size:11px; line-height:1.7; }
.condition-no-value { padding:8px 0; }
.condition-hint { grid-column:2 / -1; color:#896320; font-size:10px; }
.condition-note { color:#7e919d; display:block; margin-top:10px; font-size:10px; line-height:1.6; }
@media(max-width:800px) { .condition-row { grid-template-columns:22px 1fr 1fr auto; } .condition-value { grid-column:2 / -1; grid-row:2; } .condition-header button:first-of-type { margin-left:0; } }
</style>
