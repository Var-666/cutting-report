<script setup>
import { useAppStore } from '../state/app.js'
const app = useAppStore()
</script>

<template>
  <ButtonIcon icon="mdi:palette-outline" tooltip-content="主题配置" aria-label="主题设置" @click="app.openThemeDrawer" />
</template>
