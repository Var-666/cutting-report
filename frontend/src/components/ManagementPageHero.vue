<script setup>
import { NCard, NTag } from 'naive-ui'
import AppIcon from './AppIcon.vue'

defineProps({
  eyebrow: { type: String, required: true },
  title: { type: String, required: true },
  description: { type: String, default: '' },
  icon: { type: String, default: 'settings' },
  statusLabel: { type: String, default: '系统状态' },
  statusValue: { type: String, required: true },
  statusNote: { type: String, default: '' },
  tone: { type: String, default: 'blue' },
})
</script>

<template>
  <NCard class="management-hero" :bordered="false">
    <div class="management-hero__body">
      <div class="management-hero__copy"><h1>{{ title }}</h1><p v-if="description">{{ description }}</p></div>
      <aside class="management-hero__status">
        <AppIcon :name="icon" :size="24" />
        <div><small>{{ statusLabel }}</small><NTag :bordered="false" type="primary">{{ statusValue }}</NTag><span v-if="statusNote">{{ statusNote }}</span></div>
      </aside>
    </div>
  </NCard>
</template>

<style scoped>
.management-hero__body{display:flex;justify-content:space-between;align-items:center;gap:24px}.management-hero__copy{min-width:0}.management-hero h1{margin:0;color:var(--text-color-1);font-size:24px;line-height:1.4;font-weight:600}.management-hero__copy p{margin:8px 0 0;color:var(--text-color-3);font-size:14px;line-height:1.7}.management-hero__status{display:flex;align-items:center;gap:12px;flex:0 0 auto;max-width:330px}.management-hero__status > div{display:flex;flex-direction:column;align-items:flex-start;gap:5px}.management-hero__status small,.management-hero__status > div > span{color:var(--text-color-3);font-size:12px;line-height:1.6}
@media(max-width:900px){.management-hero__body{align-items:flex-start;flex-direction:column;gap:16px}.management-hero__status{max-width:100%}}
</style>
