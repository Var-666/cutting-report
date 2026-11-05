<script setup lang="ts">
import { computed, shallowRef, watch } from 'vue'
import { RouterView, useRouter } from 'vue-router'

const router = useRouter()
const reportRoute = shallowRef(router.currentRoute.value)
const active = computed(() => router.currentRoute.value.matched[1]?.path === '/admin/reports')
// A cached report outlet must not render another module's nested routes offscreen.
watch(router.currentRoute, route => {
  if (route.matched[1]?.path === '/admin/reports') reportRoute.value = route
}, { flush: 'sync' })
</script>

<template>
  <RouterView :route="reportRoute" v-slot="{ Component, route }">
    <KeepAlive :max="3">
      <component v-if="route.meta.keepAlive" :is="Component" :key="route.name" />
    </KeepAlive>
    <component v-if="active && !route.meta.keepAlive" :is="Component" :key="route.name" />
  </RouterView>
</template>
