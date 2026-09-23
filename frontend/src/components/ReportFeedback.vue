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
    <span class="report-feedback__icon"><AppIcon :name="icon" :size="17" /></span>
    <div><strong>{{ title }}</strong><span v-if="message">{{ message }}</span></div>
    <button v-if="actionLabel" class="report-feedback__action" type="button" @click="$emit('action')">{{ actionLabel }}</button>
    <button v-if="dismissible" class="report-feedback__dismiss" type="button" aria-label="关闭提示" @click="$emit('dismiss')">×</button>
  </aside>
</template>

<style scoped>
.report-feedback{--feedback-color:#1f7894;--feedback-border:#c9e0e6;--feedback-bg:#f0f8fa;display:flex;align-items:center;gap:11px;margin:10px 0;padding:10px 12px;color:#496b7d;border:1px solid var(--feedback-border);border-left:3px solid var(--feedback-color);border-radius:8px;background:var(--feedback-bg)}
.report-feedback--warning{--feedback-color:#c4872c;--feedback-border:#ead8b8;--feedback-bg:#fff8eb}.report-feedback--error{--feedback-color:#ce5057;--feedback-border:#eed0d2;--feedback-bg:#fff4f4}.report-feedback--success{--feedback-color:#1d9b70;--feedback-border:#c9e4d8;--feedback-bg:#eef9f5}
.report-feedback__icon{width:28px;height:28px;display:grid;place-items:center;flex:0 0 auto;color:var(--feedback-color);border-radius:7px;background:#fff}.report-feedback>div{min-width:0;display:flex;align-items:baseline;gap:9px;flex:1}.report-feedback strong{color:#274b61;font-size:11px;white-space:nowrap}.report-feedback>div>span{font-size:10px;line-height:1.55}
.report-feedback button{min-height:30px;padding:0 10px;color:var(--feedback-color);border-color:var(--feedback-border);border-radius:6px;background:#fff;font-size:10px;font-weight:700}.report-feedback__dismiss{width:30px;padding:0!important;border:0!important;background:transparent!important;font-size:18px!important;font-weight:400!important}
@media(max-width:640px){.report-feedback{align-items:flex-start;flex-wrap:wrap}.report-feedback>div{align-items:flex-start;flex-direction:column;gap:2px}.report-feedback__action{margin-left:39px}.report-feedback__dismiss{margin-left:auto}}
</style>
