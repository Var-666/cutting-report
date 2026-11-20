<script setup lang="ts">
import { toError } from '../utils/errors'

import { onBeforeUnmount, onDeactivated, ref } from 'vue'
import { downloadReportPdf } from '../api/reportExport.ts'

const props = defineProps({ reportId: { type: String, required: true }, compact: Boolean })
const loading = ref(false)
const error = ref('')
let controller: AbortController | undefined

async function download() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  controller = new AbortController()
  try {
    await downloadReportPdf(props.reportId, { signal: controller.signal })
  } catch (caught) {
    const exception = toError(caught)
    if (exception.name !== 'AbortError') error.value = `${exception.message}。可重试 PDF，或下载已归档的 HTML。`
  } finally {
    loading.value = false
    controller = undefined
  }
}
onDeactivated(() => controller?.abort())
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <span class="pdf-download" :class="{ 'pdf-download--compact': compact }">
    <NButton attr-type="button" :class="{ compact }" :text="compact" :loading="loading" :disabled="loading" @click="download">{{ loading ? '准备 PDF…' : '下载 PDF' }}</NButton>
    <span v-if="error" class="pdf-error" role="alert" :title="error">{{ error }}</span>
  </span>
</template>

<style scoped>
.pdf-download { display:inline-flex; flex-wrap:wrap; align-items:center; gap:6px; }
.pdf-download--compact { flex-wrap:nowrap; }
.pdf-download--compact .pdf-error { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.pdf-error { max-width:280px; color:rgb(var(--error-color)); font-size:12px; white-space:normal; }
</style>
