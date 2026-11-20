<script setup lang="ts">
import type { ReportCacheConfiguration } from '../api/generated/types.gen'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NAlert, NButton, NInputNumber } from 'naive-ui'
import { getReportCacheConfiguration, saveReportCacheConfiguration } from '../api/configuration'
import { useUnsavedChanges } from '../composables/useUnsavedChanges'
import { toError } from '../utils/errors'

const fields = [
  { key: 'current_seconds', label: '当期统计有效期（秒）', min: 0 },
  { key: 'history_seconds', label: '历史统计有效期（秒）', min: 0 },
  { key: 'query_seconds', label: '服务端查询缓存（秒）', min: 0 },
  { key: 'refresh_seconds', label: '后台刷新周期（秒）', min: 30 },
] as const
const draft = ref<ReportCacheConfiguration | null>(null)
const saved = ref<ReportCacheConfiguration | null>(null)
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const message = ref('')
const controller = new AbortController()
const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(saved.value))
useUnsavedChanges(dirty, '离开将丢弃尚未保存的统计缓存配置。', saving)
async function load() {
  if (loading.value || saving.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await getReportCacheConfiguration(controller.signal)
    saved.value = { ...result }
    draft.value = { ...result }
  } catch (caught) { if (!controller.signal.aborted) error.value = toError(caught).message }
  finally { loading.value = false }
}
function update(key: keyof ReportCacheConfiguration, value: number | null) {
  if (draft.value && value !== null) { draft.value[key] = value; message.value = '' }
}
async function save() {
  if (!draft.value || saving.value) return
  const submitted = { ...draft.value }
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await saveReportCacheConfiguration(submitted, controller.signal)
    saved.value = submitted
    message.value = result.message
  } catch (caught) { if (!controller.signal.aborted) error.value = toError(caught).message }
  finally { saving.value = false }
}
onMounted(load)
onBeforeUnmount(() => controller.abort())
</script>

<template>
  <section class="report-cache-settings" aria-labelledby="report-cache-heading" :aria-busy="loading || saving">
    <header><h3 id="report-cache-heading">统计缓存与刷新</h3><span>切割、焊接、能耗、PDM 共用</span></header>
    <p v-if="loading" role="status">正在读取全局配置…</p>
    <form v-if="draft" @submit.prevent="save">
      <div class="report-cache-fields">
        <label v-for="field in fields" :key="field.key">
          <span>{{ field.label }}</span>
          <NInputNumber :value="draft[field.key]" :disabled="saving" :min="field.min" :max="2147483647"
            :precision="0" :input-props="{ 'aria-label': field.label }" @update:value="value => update(field.key, value)" />
        </label>
      </div>
      <p>当期、历史有效期填 0 时分别沿用 300 秒、86400 秒；查询缓存填 0 表示关闭。页面缓存统一为 5 分钟，并受结果有效期限制。</p>
      <div class="report-cache-actions"><span>保存后新操作自动采用新规则，在途操作使用原配置。</span>
        <NButton attr-type="submit" type="primary" :loading="saving" :disabled="!dirty || saving">保存全局配置</NButton>
      </div>
    </form>
    <NAlert v-if="error" type="error" title="统计缓存配置操作失败" role="alert">{{ error }}<NButton v-if="!draft" size="small" :loading="loading" @click="load">重试</NButton></NAlert>
    <NAlert v-if="message" type="success" role="status">{{ message }}</NAlert>
  </section>
</template>

<style scoped>
.report-cache-settings { display:grid; gap:16px; padding:24px; border:1px solid var(--theme-border-color); border-radius:var(--card-radius); background:rgb(var(--container-bg-color)); }
header, .report-cache-actions { display:flex; align-items:center; justify-content:space-between; gap:16px; flex-wrap:wrap; }
h3 { margin:0; font-size:var(--font-section-title); }
header > span, p, .report-cache-actions > span { margin:0; color:var(--text-color-3); font-size:var(--font-auxiliary); }
form { display:grid; gap:16px; }
.report-cache-fields { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; }
label { display:grid; gap:8px; font-size:var(--font-control); }
@media (max-width:900px) { .report-cache-fields { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:540px) { .report-cache-settings { padding:16px; } .report-cache-fields { grid-template-columns:minmax(0,1fr); } }
</style>
