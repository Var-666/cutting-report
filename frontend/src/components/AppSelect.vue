<script setup lang="ts" generic="T extends string | number | boolean = string">
import type { ChangeEvent } from '../types/api'
import type { SelectInst, SelectProps } from 'naive-ui'

import { computed, onDeactivated, ref, useAttrs, useSlots, watch } from 'vue'
import { NSelect } from 'naive-ui'
import { selectOptions } from '../utils/selectOptions.ts'

defineOptions({ inheritAttrs: false })
const props = withDefaults(defineProps<{
  modelModifiers?: { number?: boolean; trim?: boolean; lazy?: boolean }
  value?: T
  modelValue?: T
  disabled?: boolean
  filterable?: boolean
  consistentMenuWidth?: boolean
  menuProps?: SelectProps['menuProps']
  required?: boolean
  id?: string
}>(), { modelValue: undefined, disabled: false, filterable: false, consistentMenuWidth: true, menuProps: undefined, required: false, id: undefined })
const emit = defineEmits<{ 'update:modelValue': [value: T]; change: [event: ChangeEvent<T>] }>()
const attrs = useAttrs()
const slots = useSlots()
const control = ref<SelectInst | null>(null)
const open = ref(false)
const invalid = ref(false)
const options = computed(() => selectOptions(slots.default?.() || []))
const currentValue = computed(() => props.modelValue !== undefined ? props.modelValue : props.value)
const selectedIndex = computed(() => options.value.findIndex((item) => String(item.sourceValue) === String(currentValue.value)))
const selectedValue = computed(() => selectedIndex.value < 0 ? options.value[0]?.value ?? null : selectedIndex.value)

function choose(index: number) {
  const value = options.value[index].sourceValue as T
  invalid.value = false
  emit('update:modelValue', value)
  emit('change', { target: { value }, value })
}

function handleInvalid() {
  invalid.value = true
  control.value?.focus()
}

watch(currentValue, () => { invalid.value = false })
watch(() => props.disabled, (disabled) => { if (disabled) open.value = false })
onDeactivated(() => { open.value = false })
</script>

<template>
  <!-- Prevent wrapping labels from moving focus to the hidden validation select. -->
  <div class="app-select" :class="[$attrs.class, { 'is-invalid': invalid }]" :style="$attrs.style" @click.prevent>
    <NSelect ref="control" :input-props="{ id, 'aria-label': ($attrs['aria-label'] as string | undefined) }" v-model:show="open" :value="selectedValue" :options="options" :consistent-menu-width="consistentMenuWidth" :menu-props="menuProps" :disabled="disabled" :filterable="filterable" :placeholder="$attrs.placeholder as string | undefined" :status="invalid ? 'error' : undefined" @update:value="choose" />
    <select class="app-control-native" :name="$attrs.name as string | undefined" :value="currentValue" :required="required" :disabled="disabled" tabindex="-1" aria-hidden="true" @invalid.prevent="handleInvalid"><slot /></select>
  </div>
</template>
