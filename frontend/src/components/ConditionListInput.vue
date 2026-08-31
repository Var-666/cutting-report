<script setup>
import { ref, watch } from 'vue'

const props = defineProps({ modelValue: { type: Array, required: true }, label: { type: String, required: true } })
const emit = defineEmits(['update:modelValue'])
const text = ref(props.modelValue.join('\n'))
const parse = (value) => value.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)

// Publish on input (not blur), but keep separators while the user is typing.
function update(value) {
  text.value = value
  emit('update:modelValue', parse(value))
}
watch(() => props.modelValue, (value) => {
  if (JSON.stringify(parse(text.value)) !== JSON.stringify(value)) text.value = value.join('\n')
}, { deep: true })
</script>

<template>
  <textarea :value="text" rows="2" required maxlength="26000" :aria-label="label" @input="update($event.target.value)" />
</template>
