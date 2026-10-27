<script setup lang="ts">
import { RouterView } from 'vue-router'
import { NConfigProvider, NMessageProvider, darkTheme, zhCN, dateZhCN, enUS, dateEnUS, useMessage, useThemeVars } from 'naive-ui'
import { computed, defineComponent, nextTick, onBeforeUnmount, watch, watchEffect } from 'vue'
import { loadSession, session } from './state/session'
import { useAppStore } from './state/app.ts'
import { useThemeStore } from './store/modules/theme'
import { getLocale } from './locales'
import { localStg } from './utils/storage'
import { reportThemeVariables } from './theme/reportVariables.ts'

const app = useAppStore()
const theme = useThemeStore()
const locale = computed(() => getLocale())
// Native controls and legacy reports consume the same reactive theme.
const ThemeFeedback = defineComponent({ setup() {
  window.$message = useMessage()
  const vars = useThemeVars()
  watchEffect(() => {
    for (const [name, value] of Object.entries(reportThemeVariables(vars.value))) document.documentElement.style.setProperty(name, value)
    document.documentElement.style.colorScheme = theme.darkMode ? 'dark' : 'light'
  })
  return () => null
} })
watch(() => app.isMobile, mobile => {
  if (mobile) {
    if (!localStg.get('backupThemeSettingBeforeIsMobile')) localStg.set('backupThemeSettingBeforeIsMobile', { layout: theme.layout.mode, siderCollapse: app.siderCollapse })
    theme.setThemeLayout('vertical')
  } else {
    const backup = localStg.get('backupThemeSettingBeforeIsMobile')
    if (backup) { theme.setThemeLayout(backup.layout); nextTick(() => { app.siderCollapse = backup.siderCollapse }); localStg.remove('backupThemeSettingBeforeIsMobile') }
  }
}, { immediate: true, flush: 'sync' })
const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)')
const updateMotion = () => { app.reducedMotion = motionQuery.matches }
updateMotion()
motionQuery.addEventListener('change', updateMotion)
onBeforeUnmount(() => motionQuery.removeEventListener('change', updateMotion))
const mobileQuery = window.matchMedia('(max-width: 767px)')
const updateViewport = () => app.setMobile(mobileQuery.matches)
updateViewport()
mobileQuery.addEventListener('change', updateViewport)
onBeforeUnmount(() => mobileQuery.removeEventListener('change', updateViewport))
</script>

<template>
  <NConfigProvider :locale="locale === 'en-US' ? enUS : zhCN" :date-locale="locale === 'en-US' ? dateEnUS : dateZhCN" :theme="theme.darkMode ? darkTheme : null" :theme-overrides="theme.naiveTheme">
    <NMessageProvider><ThemeFeedback />
    <div v-if="!session.ready" class="auth-loading" role="status" aria-live="polite">正在检查登录状态…</div>
    <div v-else-if="session.error && !session.user" class="auth-loading auth-failed" role="alert">
      <strong>系统初始化失败</strong><span>{{ session.error }}</span><button type="button" @click="loadSession({ force: true })">重新连接</button>
    </div>
    <RouterView v-else />
    </NMessageProvider>
  </NConfigProvider>
</template>
