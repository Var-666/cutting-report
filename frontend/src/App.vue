<script setup>
import { RouterView } from 'vue-router'
import { session } from './state/session'
</script>

<template>
  <div v-if="!session.ready" class="auth-loading">正在检查登录状态...</div>
  <div v-else-if="session.error && !session.user" class="auth-loading auth-failed">
    <strong>系统初始化失败</strong><span>{{ session.error }}</span>
  </div>
  <RouterView v-else />
</template>
