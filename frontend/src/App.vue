<script setup>
import { RouterView } from 'vue-router'
import { loadSession, session } from './state/session'
</script>

<template>
  <div v-if="!session.ready" class="auth-loading" role="status" aria-live="polite">正在检查登录状态…</div>
  <div v-else-if="session.error && !session.user" class="auth-loading auth-failed" role="alert">
    <strong>系统初始化失败</strong><span>{{ session.error }}</span><button type="button" @click="loadSession({ force: true })">重新连接</button>
  </div>
  <RouterView v-else />
</template>
