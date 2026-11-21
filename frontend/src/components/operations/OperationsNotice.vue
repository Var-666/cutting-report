<script setup lang="ts">
import { reportAsyncError } from '../../api/browserObservations.ts'

import { useDocumentVisibility } from '@vueuse/core'
import { NButton } from 'naive-ui'
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { operations } from '../../api/operations'
import AppIcon from '../AppIcon.vue'

const router = useRouter()
const pending = ref(false)
watch(useDocumentVisibility(), (visibility, _, onCleanup) => {
  if (visibility !== 'visible') return

  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  onCleanup(() => {
    clearTimeout(timer)
    controller.abort()
  })

  async function refresh() {
    try {
      const result = await operations.pendingNotice(controller.signal)
      if (!controller.signal.aborted) pending.value = result
    } catch {
      // Keep the last confirmed indication when the status database is unavailable.
    } finally {
      if (!controller.signal.aborted) timer = setTimeout(refresh, 60_000)
    }
  }
  refresh().catch(reportAsyncError)
}, { immediate: true })
</script>

<template>
  <span class="sr-only" aria-live="polite">{{ pending ? '有待处理的系统异常' : '' }}</span>
  <NButton v-if="pending" quaternary type="warning" aria-label="待处理异常" title="待处理异常"
    @click="router.push({ name: 'operations-incidents', query: { status: 'open' } })">
    <template #icon><AppIcon name="bell" :size="20" tone="current" /></template>
    <span class="lt-sm:hidden">待处理异常</span>
  </NButton>
</template>
