<script setup lang="ts">
import type { User } from '../types/auth'

import { useRoute, useRouter } from 'vue-router'
import LoginView from './LoginView.vue'
import { completeAuthentication, roleHome, session } from '../state/session'

const route = useRoute()
const router = useRouter()

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
    @authenticated="authenticated"
  />
</template>
