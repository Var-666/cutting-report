<script setup lang="ts">
import { NCard } from 'naive-ui'
import AppIcon from '../AppIcon.vue'

defineProps<{ title: string; icon: string }>()
</script>

<template>
  <NCard tag="header" size="small" class="operations-heading">
    <div class="page-heading">
      <div class="operations-heading__title">
        <span class="operations-heading__icon"><AppIcon :name="icon" :size="22" tone="current" /></span>
        <h1 class="page-heading__title">{{ title }}</h1>
      </div>
      <div class="page-heading__actions"><slot name="actions" /></div>
    </div>
    <slot />
  </NCard>
</template>

<style scoped>
.operations-heading { border-radius:var(--report-panel-radius); box-shadow:var(--theme-shadow); }
.operations-heading__title { display:flex; align-items:center; gap:var(--space-3); min-width:0; }
.operations-heading__icon { display:grid; flex:none; place-items:center; width:var(--control-height); height:var(--control-height); border-radius:var(--card-radius); color:rgb(var(--primary-color)); background:rgb(var(--primary-color) / .1); }
</style>
