<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { NAlert, NButton, NCard, NInput, NSelect, NSwitch } from 'naive-ui'
import type { LlmConfiguration } from '../api/generated/types.gen'
import { getLlmConfiguration, listLlmModels, saveLlmConfiguration } from '../api/configuration'
import { toError } from '../utils/errors'

const configuration = ref<LlmConfiguration | null>(null)
const draft = reactive({ enabled: false, base_url: '', model: '', api_key: '' })
const models = ref<string[]>([])
const loading = ref(false)
const saving = ref(false)
const catalogLoading = ref(false)
const error = ref('')
const message = ref('')
const busy = computed(() => loading.value || saving.value || catalogLoading.value)
const options = computed(() => [...new Set([...models.value, draft.model].filter(Boolean))]
  .map(model => ({ label: model, value: model })))
const controller = new AbortController()
let disposed = false

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await getLlmConfiguration({ signal: controller.signal })
    if (disposed) return
    configuration.value = result
    Object.assign(draft, { enabled: result.enabled, base_url: result.base_url, model: result.model, api_key: '' })
  } catch (caught) {
    if (!disposed) error.value = toError(caught).message
  } finally {
    if (!disposed) loading.value = false
  }
}

async function readModels() {
  catalogLoading.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await listLlmModels({ base_url: draft.base_url, api_key: draft.api_key || undefined }, { signal: controller.signal })
    if (disposed) return
    models.value = result.models
    message.value = result.models.length ? `已读取 ${result.models.length} 个模型，请选择用于智能分析的模型。` : '接口未返回可用模型，可手工输入模型 ID。'
  } catch (caught) {
    if (!disposed) error.value = toError(caught).message
  } finally {
    if (!disposed) catalogLoading.value = false
  }
}

async function save() {
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    const result = await saveLlmConfiguration({ ...draft, api_key: draft.api_key || undefined }, { signal: controller.signal })
    if (disposed) return
    configuration.value = result
    draft.api_key = ''
    message.value = '模型配置已保存。'
  } catch (caught) {
    if (!disposed) error.value = toError(caught).message
  } finally {
    if (!disposed) saving.value = false
  }
}

function changeBaseUrl(value: string) {
  draft.base_url = value
  models.value = []
  message.value = ''
}

onMounted(load)
onBeforeUnmount(() => { disposed = true; controller.abort() })
</script>

<template>
  <NCard class="model-configuration" :bordered="false" aria-label="智能分析模型配置" :aria-busy="busy">
    <header class="model-configuration-heading">
      <h2>智能分析模型</h2>
      <span v-if="configuration">当前 Web 模型 <strong>{{ configuration.active_enabled ? configuration.active_model || '未配置' : '未启用' }}</strong></span>
    </header>
    <NAlert v-if="configuration?.restart_required" type="warning" :show-icon="false">
      已保存配置待重启生效，请重启 Web、Scheduler、全部 Consumer 和 Automation。各进程生效状态可在“配置诊断”中查看。
    </NAlert>
    <NAlert v-if="error" type="error" :show-icon="false">
      {{ error }} <NButton v-if="!configuration" text @click="load" :disabled="busy">重新读取</NButton>
    </NAlert>
    <NAlert v-if="message" type="success" :show-icon="false">{{ message }}</NAlert>
    <form v-if="configuration" class="model-configuration-form" @submit.prevent="save">
      <label class="model-enabled"><NSwitch v-model:value="draft.enabled" :disabled="busy" aria-label="启用智能分析" /><span>启用智能分析</span></label>
      <label class="model-endpoint"><span>接口地址</span>
        <NInput :value="draft.base_url" @update:value="changeBaseUrl" :disabled="busy" placeholder="https://maas-api.cmhk.com/v1" aria-label="模型接口地址" />
      </label>
      <label><span>接口密钥</span>
        <NInput v-model:value="draft.api_key" type="password" show-password-on="click" :disabled="busy" autocomplete="new-password"
          :placeholder="configuration.api_key_configured ? '已配置，留空保留原密钥' : '输入对应接口密钥'" aria-label="模型接口密钥" />
      </label>
      <label><span>分析模型</span>
        <NSelect v-model:value="draft.model" :options="options" filterable tag :disabled="busy"
          placeholder="选择或输入模型 ID" aria-label="分析模型" />
      </label>
      <div class="model-configuration-actions">
        <NButton attr-type="button" @click="readModels" :loading="catalogLoading" :disabled="busy || !draft.base_url">获取模型列表</NButton>
        <NButton attr-type="submit" type="primary" :loading="saving" :disabled="busy">保存配置</NButton>
      </div>
    </form>
    <p v-else-if="loading" role="status">正在读取模型配置…</p>
  </NCard>
</template>

<style scoped>
.model-configuration-heading { display:flex; align-items:center; flex-wrap:wrap; justify-content:space-between; gap:8px 16px; margin-bottom:16px; }
.model-configuration-heading h2 { margin:0; font-size:var(--font-section-title); }
.model-configuration-heading > span { color:var(--text-color-2); font-size:var(--font-auxiliary); }
.model-configuration-heading strong { margin-left:8px; color:var(--text-color-1); overflow-wrap:anywhere; }
.model-configuration :deep(.n-alert) { margin-bottom:12px; }
.model-configuration-form { display:grid; grid-template-columns:minmax(260px,1.3fr) minmax(180px,1fr) minmax(180px,1fr); align-items:end; gap:16px; }
.model-configuration-form > label { min-width:0; }
.model-configuration-form > label > span { display:block; margin-bottom:6px; font-size:var(--font-auxiliary); }
.model-configuration-form .model-enabled { grid-column:1/-1; display:flex; align-items:center; gap:8px; }
.model-configuration-form .model-enabled > span { margin:0; }
.model-configuration-actions { grid-column:1/-1; display:flex; justify-content:flex-end; flex-wrap:wrap; gap:8px; }
@media(max-width:900px) { .model-configuration-form { grid-template-columns:repeat(2,minmax(0,1fr)); }.model-endpoint { grid-column:1/-1; } }
@media(max-width:560px) { .model-configuration-form { grid-template-columns:minmax(0,1fr); } }
</style>
