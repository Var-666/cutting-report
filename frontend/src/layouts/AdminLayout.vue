<script setup>
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import { endSession, session } from '../state/session'

const route = useRoute()
const router = useRouter()
const dataSourceLabel = computed(() => ({
  demo: '演示模式',
  excel: 'TASK.xlsx 本地数据',
  sqlserver: 'SQL Server',
}[session.systemInfo?.data_source_mode] || '数据源检查中'))
const reportsActive = computed(() => route.path.startsWith('/admin/reports'))

async function logout() {
  await endSession()
  await router.replace('/login')
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <RouterLink class="brand-link" to="/admin/home" aria-label="返回系统首页">
        <span class="brand-mark"><span></span><span></span><span></span></span>
        <span class="brand-copy"><small>PRODUCTION REPORTING</small><strong>切割生产智能产报系统</strong></span>
      </RouterLink>
      <nav class="topnav" aria-label="主导航">
        <RouterLink to="/admin/home">首页</RouterLink>
        <RouterLink to="/admin/reports/leadership" :class="{ active: reportsActive }">报表</RouterLink>
        <RouterLink to="/admin/database">数据库管理</RouterLink>
        <RouterLink to="/admin/designer">报表设计</RouterLink>
      </nav>
      <div class="account-menu">
        <small>V{{ session.systemInfo?.version || '0.1.0' }} · {{ dataSourceLabel }}</small>
        <span>{{ session.user?.display_name }}</span>
        <button type="button" @click="logout">退出</button>
      </div>
    </header>
    <main class="admin-main"><RouterView /></main>
    <footer>切割生产智能产报系统 · 内网生产数据仅供授权人员使用</footer>
  </div>
</template>
