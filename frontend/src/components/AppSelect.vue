<script setup>
import { computed, onDeactivated, ref, useAttrs, useSlots, watch } from 'vue'
import { NSelect } from 'naive-ui'
import { selectOptions } from '../utils/selectOptions.js'

defineOptions({ inheritAttrs: false })
const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: undefined },
  disabled: Boolean,
  filterable: Boolean,
  required: Boolean,
  id: { type: String, default: undefined },
})
const emit = defineEmits(['update:modelValue', 'change'])
const attrs = useAttrs()
const slots = useSlots()
const control = ref(null)
const open = ref(false)
const invalid = ref(false)
const options = computed(() => selectOptions(slots.default?.() || []))
const currentValue = computed(() => props.modelValue !== undefined ? props.modelValue : attrs.value)
const selectedIndex = computed(() => options.value.findIndex((item) => String(item.sourceValue) === String(currentValue.value)))
const selectedValue = computed(() => selectedIndex.value < 0 ? options.value[0]?.value ?? null : selectedIndex.value)

function choose(index) {
  const value = options.value[index].sourceValue
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
    <NSelect ref="control" :input-props="{ id, 'aria-label': $attrs['aria-label'] }" v-model:show="open" :value="selectedValue" :options="options" :disabled="disabled" :filterable="filterable" :placeholder="$attrs.placeholder" :status="invalid ? 'error' : undefined" @update:value="choose" />
    <select class="app-control-native" :name="$attrs.name" :value="currentValue" :required="required" :disabled="disabled" tabindex="-1" aria-hidden="true" @invalid.prevent="handleInvalid"><slot /></select>
  </div>
</template>
