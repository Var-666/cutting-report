<script setup lang="ts">
import type { PropType } from 'vue'

import { NAlert, NButton, NEmpty, NSpin } from 'naive-ui'

defineProps({
  type: { type: String as PropType<'loading' | 'empty' | 'error'>, default: 'empty', validator: (value: unknown) => ['loading', 'empty', 'error'].includes(String(value)) },
  title: { type: String, required: true },
  message: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
})

defineEmits<{ action: [] }>()

</script>

<template>
  <div class="report-panel-state" :class="`report-panel-state--${type}`" :role="type === 'error' ? 'alert' : 'status'" aria-live="polite">
    <NSpin v-if="type === 'loading'" size="small" />
    <NAlert v-if="type === 'error'" type="error" :title="title">{{ message }}</NAlert>
    <NEmpty v-else-if="type === 'empty'" :description="title"><template #extra>{{ message }}</template></NEmpty>
    <div v-else><strong>{{ title }}</strong><p v-if="message">{{ message }}</p></div>
    <NButton v-if="actionLabel" size="small" @click="$emit('action')">{{ actionLabel }}</NButton>
  </div>
</template>

<style scoped>
.report-panel-state{min-width:0;display:flex;align-items:center;justify-content:center;gap:12px;padding:28px;color:var(--text-color-3);text-align:left;background:rgb(var(--container-bg-color))}
.report-panel-state>div{min-width:0;max-width:430px;overflow-wrap:anywhere}.report-panel-state strong{display:block;color:var(--text-color-1);font-size:13px}.report-panel-state p{margin:4px 0 0;color:var(--muted);font-size:var(--font-auxiliary);line-height:1.6}
.report-panel-state :deep(.n-alert){flex:1}.report-panel-state>.n-button{flex-shrink:0}
@media(max-width:560px){.report-panel-state{align-items:flex-start;flex-direction:column;padding:20px}.report-panel-state>.n-button{width:100%;margin:4px 0 0}}
</style>
