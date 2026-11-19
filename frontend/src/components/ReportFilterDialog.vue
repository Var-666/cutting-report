<script setup lang="ts">
import { NModal } from 'naive-ui'

defineProps<{ title: string; inline?: boolean }>()
const show = defineModel<boolean>('show', { required: true })
</script>

<template>
  <slot v-if="inline" />
  <NModal v-else v-model:show="show" preset="card" :title="title" :aria-label="title"
    style="width:min(760px, calc(100vw - 32px))" :content-style="{ maxHeight: 'calc(100dvh - 128px)', overflowY: 'auto' }">
    <slot />
  </NModal>
</template>
