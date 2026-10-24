<script setup>
import { computed, onDeactivated, ref, watch } from 'vue'
import { NDatePicker } from 'naive-ui'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: String, default: '' },
  mode: { type: String, default: 'date', validator: (value) => ['date', 'month'].includes(value) },
  disabled: Boolean,
  required: Boolean,
  id: { type: String, default: undefined },
  min: { type: String, default: '' },
  max: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])
const control = ref(null)
const open = ref(false)
const invalid = ref(false)
const valueFormat = computed(() => props.mode === 'month' ? 'yyyy-MM' : 'yyyy-MM-dd')

function isOutsideBounds(timestamp) {
  const date = new Date(timestamp)
  const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}${props.mode === 'month' ? '' : `-${String(date.getDate()).padStart(2, '0')}`}`
  return Boolean((props.min && value < props.min) || (props.max && value > props.max))
}

function commit(value) {
  invalid.value = false
  const next = value || ''
  emit('update:modelValue', next)
  emit('change', { target: { value: next }, value: next })
}

function handleInvalid() {
  invalid.value = true
  control.value?.focus()
}

watch(() => props.modelValue, () => { invalid.value = false })
watch(() => props.disabled, (disabled) => { if (disabled) open.value = false })
onDeactivated(() => { open.value = false })
</script>

<template>
  <div class="app-date-picker" :class="[$attrs.class, { 'is-invalid': invalid }]" :style="$attrs.style">
    <NDatePicker :id="id" ref="control" v-model:show="open" :type="mode" :formatted-value="modelValue || null" :value-format="valueFormat" :format="mode === 'month' ? 'yyyy年MM月' : 'yyyy年MM月dd日'" :disabled="disabled" :status="invalid ? 'error' : undefined" :is-date-disabled="isOutsideBounds" :input-readonly="true" :clearable="!required" :aria-label="$attrs['aria-label'] || (mode === 'month' ? '选择月份' : '选择日期')" @update:formatted-value="commit" />
    <input class="app-control-native" :type="mode" :name="$attrs.name" :value="modelValue" :required="required" :disabled="disabled" :min="min" :max="max" tabindex="-1" aria-hidden="true" @invalid.prevent="handleInvalid" />
  </div>
</template>
