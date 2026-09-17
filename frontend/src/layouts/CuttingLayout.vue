<script setup>
import { RouterView } from 'vue-router'
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <KeepAlive :max="1">
      <component :is="Component" :key="route.name" />
    </KeepAlive>
  </RouterView>
</template>
