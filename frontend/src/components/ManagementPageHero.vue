<script setup lang="ts">
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
    <div class="page-heading">
      <div class="management-hero__copy"><h1 class="page-heading__title">{{ title }}</h1><p v-if="description">{{ description }}</p></div>
      <aside class="management-hero__status">
        <AppIcon :name="icon" :size="24" />
        <div><small>{{ statusLabel }}</small><NTag :bordered="false" type="primary">{{ statusValue }}</NTag><span v-if="statusNote">{{ statusNote }}</span></div>
      </aside>
    </div>
  </NCard>
</template>

<style scoped>
.management-hero__copy{flex:1 1 320px;min-width:0}.management-hero__copy p{margin:var(--space-2) 0 0;color:var(--text-color-3);font-size:var(--font-page-description);line-height:1.7}.management-hero__status{display:flex;align-items:center;gap:var(--space-3);max-width:min(330px,100%)}.management-hero__status > div{display:flex;flex-direction:column;align-items:flex-start;gap:var(--space-1);min-width:0;overflow-wrap:anywhere}.management-hero__status small,.management-hero__status > div > span{color:var(--text-color-3);font-size:var(--font-auxiliary);line-height:1.6}
</style>
