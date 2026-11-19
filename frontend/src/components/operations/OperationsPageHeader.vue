<script setup lang="ts">
import { NCard } from 'naive-ui'
import AppIcon from '../AppIcon.vue'

defineProps<{ title: string; icon: string }>()
</script>

<template>
  <NCard tag="header" size="small" class="operations-heading">
    <div class="operations-heading__row">
      <div class="operations-heading__title">
        <span class="operations-heading__icon"><AppIcon :name="icon" :size="22" tone="current" /></span>
        <h1>{{ title }}</h1>
      </div>
      <div class="operations-heading__actions"><slot name="actions" /></div>
    </div>
    <slot />
  </NCard>
</template>

<style scoped>
.operations-heading { border-radius:var(--report-panel-radius); box-shadow:var(--theme-shadow); }
.operations-heading__row, .operations-heading__title, .operations-heading__actions { display:flex; align-items:center; gap:var(--space-3); min-width:0; }
.operations-heading__row { flex-wrap:wrap; justify-content:space-between; }
.operations-heading__actions { flex-wrap:wrap; gap:var(--space-2); }
.operations-heading__icon { display:grid; flex:none; place-items:center; width:40px; height:40px; border-radius:var(--card-radius); color:rgb(var(--primary-color)); background:rgb(var(--primary-color) / .1); }
.operations-heading h1 { margin:0; color:var(--text-color-1); font-size:var(--font-page-title); line-height:1.3; }
</style>
