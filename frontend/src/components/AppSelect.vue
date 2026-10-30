<script setup lang="ts" generic="T extends string | number | boolean = string">
import type { ChangeEvent } from '../types/api'
import type { SelectInst, SelectProps } from 'naive-ui'
import type { ObjectDirective } from 'vue'

import { computed, nextTick, onDeactivated, ref, useAttrs, useId, useSlots, watch } from 'vue'
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
const menuId = `select-${useId()}`
const root = ref<HTMLElement | null>(null)
const options = computed(() => selectOptions(slots.default?.() || []))
const currentValue = computed(() => props.modelValue !== undefined ? props.modelValue : props.value)
const selectedIndex = computed(() => options.value.findIndex((item) => String(item.sourceValue) === String(currentValue.value)))
const selectedValue = computed(() => selectedIndex.value < 0 ? options.value[0]?.value ?? null : selectedIndex.value)

// Naive's non-filterable selection does not forward inputProps to its focus target.
function syncAccessibility(el: HTMLElement) {
  const target = el.querySelector<HTMLElement>(props.filterable ? 'input.n-base-selection-input' : '.n-base-selection-label')
  if (!target) return
  for (const icon of el.querySelectorAll('.n-base-loading')) icon.setAttribute('aria-hidden', 'true')
  const label = el.closest('label') || (props.id ? Array.from(el.ownerDocument.querySelectorAll('label')).find(item => item.htmlFor === props.id) : null)
  const caption = label?.querySelector<HTMLElement>(':scope > span') || label
  if (caption && !caption.id) caption.id = `${menuId}-label`
  const active = open.value ? el.ownerDocument.getElementById(menuId)?.querySelector('.n-base-select-option--pending') : null
  const attributes = {
    id: props.id || `${menuId}-control`, role: 'combobox', tabindex: props.disabled ? '-1' : '0',
    'aria-label': attrs['aria-label'], 'aria-labelledby': attrs['aria-labelledby'] || (!attrs['aria-label'] ? caption?.id : undefined),
    'aria-describedby': attrs['aria-describedby'], 'aria-expanded': String(open.value), 'aria-haspopup': 'listbox',
    'aria-controls': open.value ? menuId : undefined, 'aria-activedescendant': active?.id,
    'aria-autocomplete': props.filterable ? 'list' : 'none', 'aria-disabled': String(Boolean(props.disabled)),
    'aria-required': String(Boolean(props.required)), 'aria-invalid': String(invalid.value),
  }
  for (const [name, value] of Object.entries(attributes)) {
    if (value === undefined || value === null) target.removeAttribute(name)
    else target.setAttribute(name, String(value))
  }
}
const vSelectAccessibility: ObjectDirective<HTMLElement> = { mounted: syncAccessibility, updated: syncAccessibility }
function syncActiveOption() { void nextTick(() => { if (root.value) syncAccessibility(root.value) }) }
const optionProps: SelectProps['nodeProps'] = option => ({
  id: `${menuId}-option-${option.value}`, role: 'option',
  'aria-selected': option.value === selectedValue.value, 'aria-disabled': Boolean(option.disabled),
})

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
  <div class="app-select" ref="root" v-select-accessibility :class="[$attrs.class, { 'is-invalid': invalid }]" :style="$attrs.style" @click.prevent @keydown="syncActiveOption" @input="syncActiveOption">
    <NSelect ref="control" v-model:show="open" :value="selectedValue" :options="options" :consistent-menu-width="consistentMenuWidth" :menu-props="{ ...menuProps, id: menuId, role: 'listbox' }" :node-props="optionProps" :disabled="disabled" :filterable="filterable" :placeholder="$attrs.placeholder as string | undefined" :status="invalid ? 'error' : undefined" @update:value="choose" />
    <select class="app-control-native" :name="$attrs.name as string | undefined" :value="currentValue" :required="required" :disabled="disabled" tabindex="-1" aria-hidden="true" @invalid.prevent="handleInvalid"><slot /></select>
  </div>
</template>
