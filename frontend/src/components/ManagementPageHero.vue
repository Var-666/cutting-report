<script setup lang="ts">
defineProps<{ title: string }>()
</script>

<template>
  <header class="page-heading">
    <h1 class="page-heading__title">{{ title }}</h1>
    <div v-if="$slots.actions" class="page-heading__actions"><slot name="actions" /></div>
  </header>
</template>
