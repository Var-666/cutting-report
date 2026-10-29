<script setup lang="ts">
import type { PropType } from 'vue'
import { NAlert, NButton } from 'naive-ui'

defineProps({
  type: { type: String as PropType<'info' | 'warning' | 'error' | 'success'>, default: 'info', validator: (value: unknown) => ['info', 'warning', 'error', 'success'].includes(String(value)) },
  title: { type: String, required: true },
  message: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
  dismissible: { type: Boolean, default: false },
})

defineEmits<{ action: []; dismiss: [] }>()

</script>

<template>
  <NAlert class="report-feedback" :class="`report-feedback--${type}`" :type="type" :title="title" :role="type === 'error' ? 'alert' : 'status'" aria-live="polite">
    <template #default>
      <div class="report-feedback__content"><span>{{ message }}</span><div v-if="actionLabel || dismissible" class="report-feedback__actions">
        <NButton v-if="actionLabel" class="report-feedback__action" size="small" @click="$emit('action')">{{ actionLabel }}</NButton>
        <NButton v-if="dismissible" class="report-feedback__dismiss" size="small" quaternary aria-label="关闭提示" @click="$emit('dismiss')">关闭</NButton>
      </div></div>
    </template>
  </NAlert>
</template>

<style scoped>
.report-feedback{margin:16px 0;overflow-wrap:anywhere}.report-feedback__content{display:flex;align-items:center;justify-content:space-between;gap:12px}.report-feedback__content>span{min-width:0}.report-feedback__actions{display:flex;gap:8px;flex-shrink:0}
@media(max-width:640px){.report-feedback__content{align-items:flex-start;flex-direction:column}.report-feedback__actions{flex-wrap:wrap}}
</style>
