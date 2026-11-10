<script setup lang="ts">
import type { User } from '../types/auth'

import { useRoute, useRouter } from 'vue-router'
import { computed } from 'vue'
import LoginView from './LoginView.vue'
import { completeAuthentication, roleHome, session } from '../state/session'

const route = useRoute()
const router = useRouter()
const ssoErrors: Record<string, string> = {
  disabled: '统一登录尚未启用，请使用账号密码登录。',
  unavailable: '统一认证服务暂时不可用，请重试或使用账号密码登录。',
  busy: '统一登录请求过多，请稍后重试。',
  state: '登录请求已过期或与当前浏览器不符，请重新登录。',
  denied: '统一认证未完成，请重新登录。',
  token: '统一认证结果验证失败，请联系管理员。',
  account: '认证身份未关联有效的本站账号，请联系管理员核实。',
}
const ssoError = computed(() => typeof route.query.sso_error === 'string'
  ? ssoErrors[route.query.sso_error] || '统一登录失败，请重新登录。'
  : '')

async function authenticated(user: User) {
  await completeAuthentication(user)
  const redirect = typeof route.query.redirect === 'string'
    && route.query.redirect.startsWith('/')
    && !route.query.redirect.startsWith('//')
    ? route.query.redirect
    : roleHome(user.role)
  await router.replace(redirect)
}
</script>

<template>
  <LoginView
    :setup-required="session.setupRequired"
    :worker-database-auth-enabled="session.workerDatabaseAuthEnabled"
    :sso-error="ssoError"
    @authenticated="authenticated"
  />
</template>
