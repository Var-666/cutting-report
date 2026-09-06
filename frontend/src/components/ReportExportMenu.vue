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
  <select class="report-export-menu" aria-label="导出报表" :disabled="disabled || loading" value="" @change="select">
    <option value="" disabled>{{ loading ? '正在导出…' : '导出报表' }}</option>
    <option value="html">导出 HTML</option>
    <option value="pdf">下载 PDF</option>
  </select>
</template>

<style scoped>
.report-export-menu { width:auto; min-width:136px; height:42px; padding:0 34px 0 14px; border:1px solid #b9cbd8; border-radius:5px; background-color:#fff; color:#34465c; font:inherit; cursor:pointer; }
.report-export-menu:disabled { cursor:wait; opacity:.6; }
.report-export-menu:focus-visible { outline:2px solid #1684ad; outline-offset:2px; }
</style>
