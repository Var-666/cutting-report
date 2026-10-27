<script setup lang="ts">
import { RouterLink } from 'vue-router'
import { NButton, NText } from 'naive-ui'
import AppIcon from './AppIcon.vue'

defineProps({
  headingId: { type: String, default: '' },
  title: { type: String, required: true },
  icon: { type: String, required: true },
  to: { type: String, default: '' },
  action: { type: String, default: '' },
})
</script>

<template>
  <header class="flex-y-center justify-between gap-12px">
    <h2 :id="headingId" class="m-0 min-w-0 flex-y-center gap-8px text-16px font-medium">
      <AppIcon :name="icon" :size="18" tone="current" class="text-primary" /><NText>{{ title }}</NText>
    </h2>
    <RouterLink v-if="to" v-slot="{ href, navigate }" :to="to" custom>
      <NButton text type="primary" tag="a" :href="href" class="shrink-0 transition-all-300" @click="navigate">{{ action }}<AppIcon name="chevron-right" :size="14" tone="current" /></NButton>
    </RouterLink>
  </header>
</template>
