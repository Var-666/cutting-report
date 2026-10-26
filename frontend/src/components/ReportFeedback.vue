<script setup>
import { computed } from 'vue'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  type: { type: String, default: 'info', validator: (value) => ['info', 'warning', 'error', 'success'].includes(value) },
  title: { type: String, required: true },
  message: { type: String, default: '' },
  actionLabel: { type: String, default: '' },
  dismissible: { type: Boolean, default: false },
})

defineEmits(['action', 'dismiss'])

const icon = computed(() => ({ info: 'activity', warning: 'alert', error: 'alert', success: 'check' }[props.type]))
</script>

<template>
  <aside class="report-feedback" :class="`report-feedback--${type}`" :role="type === 'error' ? 'alert' : 'status'" aria-live="polite">
    <span class="report-feedback__icon"><AppIcon :name="icon" :size="17" tone="current" /></span>
    <div><strong>{{ title }}</strong><span v-if="message">{{ message }}</span></div>
    <button v-if="actionLabel" class="report-feedback__action" type="button" @click="$emit('action')">{{ actionLabel }}</button>
    <button v-if="dismissible" class="report-feedback__dismiss" type="button" aria-label="关闭提示" @click="$emit('dismiss')">×</button>
  </aside>
</template>

<style scoped>
.report-feedback{--feedback-color:rgb(var(--info-color));--feedback-border:rgb(var(--info-color));--feedback-bg:rgb(var(--info-color) / .1);display:flex;align-items:center;gap:12px;margin:16px 0;padding:12px;color:var(--text-color-3);border:1px solid var(--feedback-border);border-left:3px solid var(--feedback-color);border-radius:8px;background:var(--feedback-bg)}
.report-feedback--warning{--feedback-color:rgb(var(--warning-color));--feedback-border:rgb(var(--warning-color));--feedback-bg:rgb(var(--warning-color) / .1)}.report-feedback--error{--feedback-color:rgb(var(--error-color));--feedback-border:rgb(var(--error-color));--feedback-bg:rgb(var(--error-color) / .1)}.report-feedback--success{--feedback-color:rgb(var(--success-color));--feedback-border:rgb(var(--success-color));--feedback-bg:rgb(var(--success-color) / .1)}
.report-feedback__icon{width:28px;height:28px;display:grid;place-items:center;flex:0 0 auto;color:var(--feedback-color);border-radius:7px;background:rgb(var(--container-bg-color))}.report-feedback>div{min-width:0;display:flex;align-items:baseline;gap:8px;flex:1}.report-feedback strong{color:var(--text-color-1);font-size:var(--font-auxiliary);white-space:nowrap}.report-feedback>div>span{font-size:var(--font-auxiliary);line-height:1.55}
.report-feedback button{min-height:30px;padding:0 12px;color:var(--feedback-color);border-color:var(--feedback-border);border-radius:6px;background:rgb(var(--container-bg-color));font-size:var(--font-auxiliary);font-weight:700}.report-feedback__dismiss{width:30px;padding:0!important;border:0!important;background:transparent!important;font-size:18px!important;font-weight:400!important}
@media(max-width:640px){.report-feedback{align-items:flex-start;flex-wrap:wrap}.report-feedback>div{align-items:flex-start;flex-direction:column;gap:4px}.report-feedback__action{margin-left:40px}.report-feedback__dismiss{margin-left:auto}}
</style>
