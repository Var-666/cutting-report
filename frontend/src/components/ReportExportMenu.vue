<script setup>
import AppIcon from './AppIcon.vue'

defineProps({ disabled: Boolean, loading: Boolean, formats: { type: Array, default: () => ['html', 'pdf'] } })
const emit = defineEmits(['select'])

function select(event) {
  const format = event.target.value
  event.target.value = ''
  if (format) emit('select', format)
}
</script>

<template>
  <div class="report-export-menu" :class="{ 'is-disabled': disabled || loading }">
    <AppIcon name="download" size="15" tone="current" />
    <AppSelect class="report-export-menu__select" aria-label="导出报表" :disabled="disabled || loading" value="" @change="select">
      <option value="" disabled>{{ loading ? '正在导出…' : '导出报表' }}</option>
      <option v-if="formats.includes('csv')" value="csv">导出 CSV 明细</option>
      <option value="html">导出 HTML</option>
      <option value="pdf">下载 PDF</option>
    </AppSelect>
  </div>
</template>

<style scoped>
.report-export-menu { position:relative; width:100%; min-width:0; height:40px; }
.report-export-menu > .app-icon { position:absolute; top:50%; left:12px; z-index:2; pointer-events:none; transform:translateY(-50%); }
.report-export-menu__select { width:100%; height:100%; }
.report-export-menu :deep(.n-base-selection) { --n-height:40px!important; }
.report-export-menu :deep(.n-base-selection-label) { padding-left:36px; border-radius:8px; font-size:11px; font-weight:700; }
.report-export-menu.is-disabled { cursor:wait; opacity:.6; }
</style>
