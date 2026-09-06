<script setup>
const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  schema: { type: Object, default: null },
  defaultLabel: { type: String, default: '文件分类、文件名升序' },
})
const emit = defineEmits(['update:modelValue'])
function patch(index, values) {
  emit('update:modelValue', props.modelValue.map((item, i) => i === index ? { ...item, ...values } : item))
}
function add() {
  const field = props.schema?.sort_fields?.find((f) => !props.modelValue.some((item) => item.field === f.key))
  if (field && props.modelValue.length < (props.schema?.max_sort_rules || 5)) {
    emit('update:modelValue', [...props.modelValue, { field: field.key, direction: 'asc' }])
  }
}
</script>

<template>
  <fieldset class="sort-builder" :aria-label="`${schema?.label || ''}排序设置`">
    <legend>排序设置</legend>
    <div class="sort-toolbar"><small>从上到下依次排序，最多 {{ schema?.max_sort_rules || 5 }} 级。{{ modelValue.length ? '' : `默认：${defaultLabel}。` }}</small><button type="button" :disabled="!schema?.sort_fields?.length || modelValue.length >= (schema?.max_sort_rules || 5)" @click="add">＋ 添加排序</button><button type="button" :disabled="!modelValue.length" @click="emit('update:modelValue', [])">恢复默认</button></div>
    <div v-for="(item, index) in modelValue" :key="index" class="sort-rule">
      <span>{{ index + 1 }}</span>
      <label><span>排序字段</span><select :value="item.field" :aria-label="`${schema?.label}排序${index + 1}字段`" @change="patch(index, { field: $event.target.value })"><option v-for="field in schema?.sort_fields || []" :key="field.key" :value="field.key" :disabled="modelValue.some((other, i) => i !== index && other.field === field.key)">{{ field.label }}</option></select></label>
      <label><span>排序方向</span><select :value="item.direction" :aria-label="`${schema?.label}排序${index + 1}方向`" @change="patch(index, { direction: $event.target.value })"><option value="asc">升序（小 → 大 / 早 → 晚）</option><option value="desc">降序（大 → 小 / 晚 → 早）</option></select></label>
      <button type="button" :aria-label="`删除${schema?.label}排序${index + 1}`" @click="emit('update:modelValue', modelValue.filter((_, i) => i !== index))">删除</button>
    </div>
  </fieldset>
</template>

<style scoped>
.sort-builder { margin:16px 0 0; padding:16px; border:1px solid var(--line); background:#f8fbfd; min-width:0; }
legend { padding:0 7px; color:var(--navy); font-size:12px; font-weight:700; }
.sort-toolbar { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }.sort-toolbar small { flex:1; color:var(--muted); font-size:11px; min-width:160px; }
.sort-rule { display:grid; grid-template-columns:20px minmax(0,1fr) minmax(0,1fr) auto; align-items:end; gap:12px; margin-top:12px; }.sort-rule > span { align-self:center; color:var(--muted); font-size:12px; }
label > span { display:block; margin-bottom:5px; color:var(--muted); font-size:11px; }select { width:100%; min-width:0; min-height:36px; font-size:12px; }
button { min-height:34px; padding:7px 12px; border:1px solid #c3d3dc; color:var(--blue); background:#fff; cursor:pointer; font-size:11px; }button:disabled { opacity:.5; cursor:not-allowed; }
@media(max-width:600px) { .sort-rule { grid-template-columns:20px minmax(0,1fr) auto; }.sort-rule label:nth-of-type(2) { grid-column:2; grid-row:2; }.sort-rule button { grid-column:3; grid-row:1 / 3; } }
</style>
