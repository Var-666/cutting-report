<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  type: { type: String, default: 'empty', validator: (value) => ['loading', 'empty', 'error'].includes(value) },
  title: { type: String, required: true },
  message: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
})

defineEmits(['action'])

const icon = computed(() => ({ loading: 'activity', empty: 'database', error: 'alert' }[props.type]))
</script>

<template>
  <div class="report-panel-state" :class="`report-panel-state--${type}`" :role="type === 'error' ? 'alert' : 'status'" aria-live="polite">
    <span class="report-panel-state__icon">
      <span v-if="type === 'loading'" class="spinner dark" aria-hidden="true"></span>
      <AppIcon v-else :name="icon" :size="20" />
    </span>
    <div>
      <strong>{{ title }}</strong>
      <p v-if="message">{{ message }}</p>
    </div>
    <button v-if="actionLabel" type="button" @click="$emit('action')">{{ actionLabel }}</button>
  </div>
</template>

<style scoped>
.report-panel-state{min-height:var(--report-trend-height,240px);display:flex;align-items:center;justify-content:center;gap:12px;padding:28px;color:#526f82;text-align:left;background:#fff}
.report-panel-state__icon{width:38px;height:38px;display:grid;place-items:center;flex:0 0 auto;color:#247fa5;border:1px solid #cfe0e7;border-radius:10px;background:#fff;box-shadow:0 6px 16px rgba(22,62,89,.07)}
.report-panel-state>div{max-width:430px}.report-panel-state strong{display:block;color:#23485f;font-size:13px}.report-panel-state p{margin:4px 0 0;color:var(--muted);font-size:11px;line-height:1.6}
.report-panel-state button{min-height:32px;margin-left:8px;padding:0 12px;color:#1d6e91;border-color:#a9c8d6;border-radius:7px;background:#fff;font-size:10px;font-weight:700}
.report-panel-state--error{color:#b8494f;background:#fff6f6}.report-panel-state--error .report-panel-state__icon{color:#cf4e55;border-color:#efd4d6;background:#fff}.report-panel-state--error strong{color:#9d3339}.report-panel-state--error p{color:#9a5b60}
@media(max-width:560px){.report-panel-state{min-height:210px;align-items:flex-start;flex-direction:column}.report-panel-state button{width:100%;margin:4px 0 0}}
</style>
