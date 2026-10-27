<script setup lang="ts">
import { RouterView, useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import SoybeanAdminLayout from '../vendor/soybean-admin/index.vue'
import projectLogo from '../assets/project-logo.png'
import DeviceSwitcher from '../components/DeviceSwitcher.vue'
import { useAppStore } from '../state/app.ts'
import { deviceSelection } from '../state/devices.ts'
import { endSession, session } from '../state/session'

const app = useAppStore()
const router = useRouter()

async function logout() {
  await endSession()
  await router.replace('/login')
}
</script>

<template>
  <div class="worker-shell soybean-shell">
    <a class="skip-link" href="#worker-main">跳到主要内容</a>
    <SoybeanAdminLayout class="soybean-layout" scroll-el-id="worker-main" :sider-visible="false" :tab-visible="false" :footer-visible="false" :header-height="app.isMobile ? 104 : 64" content-class="min-w-0">
      <template #header>
        <div class="soybean-header soybean-worker-header">
          <div class="soybean-brand"><img class="brand-logo" :src="projectLogo" alt="" width="36" height="36" /><strong>工人任务确认系统</strong></div>
          <div class="soybean-header-actions">
            <DeviceSwitcher />
            <span class="soybean-account">{{ session.user?.display_name }}</span>
            <NButton quaternary size="small" @click="logout">退出</NButton>
          </div>
        </div>
      </template>
      <div class="worker-main soybean-page"><RouterView v-if="deviceSelection.ready" :key="deviceSelection.id" /></div>
    </SoybeanAdminLayout>
  </div>
</template>
