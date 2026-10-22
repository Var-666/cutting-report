<script setup>
import { computed } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'
import projectLogo from '../assets/project-logo.png'
import AppIcon from '../components/AppIcon.vue'
import DeviceSwitcher from '../components/DeviceSwitcher.vue'
import { deviceSelection } from '../state/devices.js'
import { endSession, session } from '../state/session'

const route = useRoute()
const router = useRouter()
const dataSourceLabel = computed(() => ({
  demo: '演示模式',
  sqlserver: 'SQL Server',
}[session.systemInfo?.data_source_mode] || '数据源检查中'))
const devicePage = computed(() => route.path === '/admin/home' || route.path.startsWith('/admin/reports') || route.path.startsWith('/admin/alarms'))
const reportsActive = computed(() => route.path.startsWith('/admin/reports'))
const alarmsActive = computed(() => route.path.startsWith('/admin/alarms'))
const settingsActive = computed(() => route.path.startsWith('/admin/settings'))
const cuttingActive = computed(() => route.path.startsWith('/admin/cutting-dispatch'))

async function logout() {
  await endSession()
  await router.replace('/login')
}
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#admin-main">跳到主要内容</a>
    <header class="topbar">
      <RouterLink class="brand-link" to="/admin/home" aria-label="返回系统首页">
        <img class="brand-logo" :src="projectLogo" alt="" width="46" height="46" />
        <span class="brand-copy"><small>PRODUCTION REPORTING</small><strong>切割生产智能产报系统</strong></span>
      </RouterLink>
      <nav class="topnav" aria-label="主导航">
        <RouterLink to="/admin/home" aria-label="首页" title="首页"><AppIcon name="home" :size="15"/><span>首页</span></RouterLink>
        <RouterLink to="/admin/reports/leadership" :class="{ active: reportsActive }" aria-label="报表" title="报表"><AppIcon name="report" :size="15"/><span>报表</span></RouterLink>
        <RouterLink to="/admin/alarms" :class="{ active: alarmsActive }" aria-label="告警" title="告警"><AppIcon name="bell" :size="15"/><span>告警</span></RouterLink>
        <RouterLink to="/admin/cutting-dispatch" :class="{ active: cuttingActive }" aria-label="文件派发" title="文件派发"><AppIcon name="send" :size="15"/><span>文件派发</span></RouterLink>
        <RouterLink to="/admin/settings" :class="{ active: settingsActive }" aria-label="设置" title="设置"><AppIcon name="settings" :size="15"/><span>设置</span></RouterLink>
      </nav>
      <div class="topbar-actions">
        <DeviceSwitcher />
        <div class="account-menu">
          <small>V{{ session.systemInfo?.version || '0.1.0' }} · {{ dataSourceLabel }}</small>
          <span class="account-name"><AppIcon name="user" :size="14"/>{{ session.user?.display_name }}</span>
          <button type="button" aria-label="退出系统" title="退出系统" @click="logout"><AppIcon name="logout" :size="13"/><span>退出</span></button>
        </div>
      </div>
    </header>
    <main id="admin-main" class="admin-main" tabindex="-1">
      <RouterView v-if="!devicePage || deviceSelection.ready" :key="devicePage ? deviceSelection.id : undefined" />
    </main>
    <footer>切割生产智能产报系统 · 内网生产数据仅供授权人员使用</footer>
  </div>
</template>
