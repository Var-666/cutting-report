<script setup lang="ts">
import { toError } from '../utils/errors'

import { ref } from 'vue'
import { downloadReportPdf } from '../api/reportExport.ts'

const props = defineProps({ reportId: { type: String, required: true }, compact: Boolean })
const loading = ref(false)
const error = ref('')

async function download() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    await downloadReportPdf(props.reportId)
  } catch (caught) {
    const exception = toError(caught)
    error.value = exception.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <span class="pdf-download">
    <button type="button" :class="{ compact }" :disabled="loading" @click="download">{{ loading ? '生成 PDF…' : '下载 PDF' }}</button>
    <span v-if="error" class="pdf-error" role="alert">{{ error }}</span>
  </span>
</template>

<style scoped>
.pdf-download { display:inline-flex; flex-wrap:wrap; align-items:center; gap:6px; }
button.compact { min-height:0; height:auto; padding:0; border:0; background:transparent; color:rgb(var(--primary-color)); font:inherit; cursor:pointer; white-space:nowrap; }
.pdf-error { max-width:280px; color:rgb(var(--error-color)); font-size:12px; white-space:normal; }
</style>
