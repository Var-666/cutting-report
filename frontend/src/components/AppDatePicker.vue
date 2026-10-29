<script setup lang="ts">
import type { ChangeEvent } from '../types/api'

import type { PropType } from 'vue'
import type { DatePickerInst } from 'naive-ui'

import { computed, onDeactivated, ref, watch } from 'vue'
import { NDatePicker } from 'naive-ui'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: String, default: '' },
  mode: { type: String as PropType<'date' | 'month'>, default: 'date', validator: (value: unknown) => ['date', 'month'].includes(String(value)) },
  disabled: Boolean,
  required: Boolean,
  id: { type: String, default: undefined },
  min: { type: String, default: '' },
  max: { type: String, default: '' },
  to: { type: String, default: undefined },
})
const emit = defineEmits<{ 'update:modelValue': [value: string]; change: [event: ChangeEvent] }>()
const control = ref<DatePickerInst | null>(null)
const open = ref(false)
const invalid = ref(false)
const valueFormat = computed(() => props.mode === 'month' ? 'yyyy-MM' : 'yyyy-MM-dd')

function isOutsideBounds(timestamp: number) {
  const date = new Date(timestamp)
  const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}${props.mode === 'month' ? '' : `-${String(date.getDate()).padStart(2, '0')}`}`
  return Boolean((props.min && value < props.min) || (props.max && value > props.max))
}

function commit(value: string | null) {
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
    <NDatePicker :id="id" ref="control" v-model:show="open" :to="to" :type="mode" :formatted-value="modelValue || null" :value-format="valueFormat" :format="mode === 'month' ? 'yyyy年MM月' : 'yyyy年MM月dd日'" :disabled="disabled" :status="invalid ? 'error' : undefined" :is-date-disabled="isOutsideBounds" :input-readonly="true" :clearable="!required" :aria-label="($attrs['aria-label'] as string | undefined) || (mode === 'month' ? '选择月份' : '选择日期')" @update:formatted-value="commit" />
    <input class="app-control-native" :type="mode" :name="$attrs.name as string | undefined" :value="modelValue" :required="required" :disabled="disabled" :min="min" :max="max" tabindex="-1" aria-hidden="true" @invalid.prevent="handleInvalid" />
  </div>
</template>
