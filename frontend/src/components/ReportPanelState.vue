<script setup lang="ts">
import type { PropType } from 'vue'

import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  type: { type: String as PropType<'loading' | 'empty' | 'error'>, default: 'empty', validator: (value: unknown) => ['loading', 'empty', 'error'].includes(String(value)) },
  title: { type: String, required: true },
  message: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
})

defineEmits<{ action: [] }>()

const icon = computed(() => ({ loading: 'activity', empty: 'database', error: 'alert' }[props.type]))
</script>

<template>
  <div class="report-panel-state" :class="`report-panel-state--${type}`" :role="type === 'error' ? 'alert' : 'status'" aria-live="polite">
    <span class="report-panel-state__icon">
      <span v-if="type === 'loading' || type === 'error'" class="spinner dark" aria-hidden="true"></span>
      <AppIcon v-else :name="icon" :size="20" tone="current" />
    </span>
    <div>
      <strong>{{ title }}</strong>
      <p v-if="message">{{ message }}</p>
    </div>
    <button v-if="actionLabel" type="button" @click="$emit('action')">{{ actionLabel }}</button>
  </div>
</template>

<style scoped>
.report-panel-state{min-width:0;display:flex;align-items:center;justify-content:center;gap:12px;padding:28px;color:var(--text-color-3);text-align:left;background:rgb(var(--container-bg-color))}
.report-panel-state__icon{width:38px;height:38px;display:grid;place-items:center;flex:0 0 auto;color:rgb(var(--info-color));border:1px solid var(--theme-border-color);border-radius:10px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow)}
.report-panel-state>div{min-width:0;max-width:430px;overflow-wrap:anywhere}.report-panel-state strong{display:block;color:var(--text-color-1);font-size:13px}.report-panel-state p{margin:4px 0 0;color:var(--muted);font-size:var(--font-auxiliary);line-height:1.6}
.report-panel-state button{min-height:32px;margin-left:8px;padding:0 12px;color:rgb(var(--info-color));border-color:var(--theme-border-color);border-radius:7px;background:rgb(var(--container-bg-color));font-size:var(--font-auxiliary);font-weight:700}
@media(max-width:560px){.report-panel-state{align-items:flex-start;flex-direction:column;padding:20px}.report-panel-state button{width:100%;margin:4px 0 0}}
</style>
