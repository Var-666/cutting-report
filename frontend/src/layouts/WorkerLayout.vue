<script setup>
import { computed } from 'vue'
import { RouterView, useRouter } from 'vue-router'
import projectLogo from '../assets/project-logo.png'
import { endSession, session } from '../state/session'

const router = useRouter()
const dataSourceLabel = computed(() => ({
  demo: '演示模式',
  excel: 'TASK.xlsx 本地数据',
  sqlserver: 'SQL Server',
}[session.systemInfo?.data_source_mode] || '数据源检查中'))

async function logout() {
  await endSession()
  await router.replace('/login')
}
</script>

<template>
  <div class="worker-shell">
    <a class="skip-link" href="#worker-main">跳到主要内容</a>
    <header class="worker-topbar">
      <img class="brand-logo" :src="projectLogo" alt="" width="46" height="46" />
      <div><p>WORKER PORTAL</p><h1>工人任务确认系统</h1></div>
      <div class="account-menu">
        <small>V{{ session.systemInfo?.version || '0.1.0' }} · {{ dataSourceLabel }}</small>
        <span>{{ session.user?.display_name }}</span>
        <button type="button" @click="logout">退出</button>
      </div>
    </header>
    <main id="worker-main" class="worker-main" tabindex="-1"><RouterView /></main>
    <footer>工人任务确认系统 · 请仅确认本人完成的生产任务</footer>
  </div>
</template>
