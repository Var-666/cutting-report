<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { PageTab } from '../../vendor/soybean-admin/materials'
import { useThemeStore } from '../../store/modules/theme'
import { localStg } from '../../utils/storage'
import AppIcon from '../../components/AppIcon.vue'
import { getTabIdByRoute } from '../../utils/themeTabs.ts'
import { deviceSelection } from '../../state/devices.ts'

const theme = useThemeStore()
const route = useRoute()
const router = useRouter()
const root = ref<HTMLElement | null>(null)
const cached = localStg.get('tabs')
const paths = ref<string[]>(theme.tab.cache && Array.isArray(cached) ? cached.filter(path => typeof path === 'string' && path.startsWith('/admin/')) : [])
const idByPath = (path: string) => getTabIdByRoute(router.resolve(path))
const activeId = computed(() => getTabIdByRoute(route))
const homePath = computed(() => deviceSelection.id === 'welding' ? '/admin/welding/home' : '/admin/home')
const tabs = computed(() => [...new Map([...new Set([homePath.value, ...paths.value])].flatMap(path => {
  const resolved = router.resolve(path)
  if (resolved.matched.some(record => record.meta.line && record.meta.line !== (deviceSelection.id === 'welding' ? 'welding' : 'cutting'))) return []
    return resolved.matched.some(record => record.meta.role === 'admin') && !resolved.matched.some(record => record.redirect) && resolved.meta.title
    ? [[idByPath(path), { id: idByPath(path), path, label: resolved.meta.title, icon: resolved.meta.icon }]] : []
})).values()])
watch(() => route.fullPath, async path => {
  const index = paths.value.findIndex(item => idByPath(item) === idByPath(path))
  if (index < 0) paths.value.push(path)
  else paths.value[index] = path
  await nextTick()
  root.value?.querySelector('[data-active="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
}, { immediate: true })
watch([paths, () => theme.tab.cache], () => {
  if (theme.tab.cache) localStg.set('tabs', paths.value)
  else localStg.remove('tabs')
}, { deep: true, immediate: true })
function close(id: string) {
  if (id === homePath.value) return
  const index = tabs.value.findIndex(tab => tab.id === id)
  const fallback = tabs.value[index - 1]?.path || homePath.value
  paths.value = paths.value.filter(item => idByPath(item) !== id)
  if (activeId.value === id) router.push(fallback)
}
function middleClick(event: MouseEvent, id: string) {
  if (event.button !== 1 || !theme.tab.closeTabByMiddleClick) return
  event.preventDefault()
  close(id)
}
function scrollTabs(event: WheelEvent) {
  const element = event.currentTarget as HTMLElement
  const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY
  const previous = element.scrollLeft
  element.scrollLeft += delta
  if (element.scrollLeft !== previous) event.preventDefault()
}
</script>
<template>
  <DarkModeContainer class="h-full min-w-0 flex-y-center px-16px shadow-tab">
    <div ref="root" class="theme-tabs h-full min-w-0 flex flex-1 overflow-x-auto" :class="theme.tab.mode === 'button' ? 'items-center gap-12px' : 'items-end'" role="tablist" aria-label="已打开页面" @wheel="scrollTabs">
      <PageTab v-for="tab in tabs" :key="tab.id" class="flex-shrink-0" :mode="theme.tab.mode" :dark-mode="theme.darkMode" :active="tab.id === activeId" :active-color="theme.themeColor" :closable="tab.id !== homePath" :data-active="tab.id === activeId" role="tab" :aria-selected="tab.id === activeId" tabindex="0" @click="router.push(tab.path)" @keydown.enter="router.push(tab.path)" @keydown.delete="close(tab.id)" @mousedown="middleClick($event, tab.id)" @close="close(tab.id)">
        <template #prefix><AppIcon :name="tab.icon || ''" :size="16" tone="current" /></template>
        <span class="max-w-240px ellipsis-text">{{ tab.label }}</span>
      </PageTab>
    </div>
  </DarkModeContainer>
</template>

<style scoped>
.theme-tabs { scrollbar-width:none; }
.theme-tabs::-webkit-scrollbar { display:none; }
</style>
