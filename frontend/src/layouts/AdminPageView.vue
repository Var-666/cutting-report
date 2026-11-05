<script setup lang="ts">
import { computed } from 'vue'
import { RouterView, useRoute } from 'vue-router'

defineProps<{ ready: boolean; deviceId: string; devicePage: boolean; transitionName: string }>()
const route = useRoute()
const reports = computed(() => route.matched[1]?.path === '/admin/reports')
</script>

<template>
  <RouterView v-if="ready" v-slot="{ Component }">
    <div class="min-w-0">
      <KeepAlive :key="deviceId" :max="1">
        <component v-if="reports" :is="Component" />
      </KeepAlive>
      <Transition :name="reports ? '' : transitionName" mode="out-in">
        <div v-if="!reports" :key="`${route.matched[1]?.path}:${devicePage ? deviceId : ''}`" class="min-w-0"><component :is="Component" /></div>
      </Transition>
    </div>
  </RouterView>
</template>
