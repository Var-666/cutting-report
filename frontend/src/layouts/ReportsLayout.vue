<script setup lang="ts">
import { RouterView } from 'vue-router'
</script>

<template>
  <RouterView v-slot="{ Component, route }">
    <KeepAlive :max="3">
      <component v-if="route.meta.keepAlive" :is="Component" :key="route.name" />
    </KeepAlive>
    <component v-if="!route.meta.keepAlive" :is="Component" :key="route.name" />
  </RouterView>
</template>
