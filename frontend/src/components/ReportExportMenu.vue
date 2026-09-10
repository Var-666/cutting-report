<script setup>
defineProps({ disabled: Boolean, loading: Boolean })
const emit = defineEmits(['select'])

function select(event) {
  const format = event.target.value
  event.target.value = ''
  if (format) emit('select', format)
}
</script>

<template>
  <AppSelect class="report-export-menu" aria-label="导出报表" :disabled="disabled || loading" value="" @change="select">
    <option value="" disabled>{{ loading ? '正在导出…' : '导出报表' }}</option>
    <option value="html">导出 HTML</option>
    <option value="pdf">下载 PDF</option>
  </AppSelect>
</template>

<style scoped>
.report-export-menu { width:136px; min-width:0; height:var(--control-height); }
.report-export-menu.is-disabled { cursor:wait; opacity:.6; }
</style>
