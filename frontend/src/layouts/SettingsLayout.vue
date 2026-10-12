<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <section class="report-navigation settings-navigation" aria-label="系统设置导航">
    <div>
      <RouterLink class="settings-home-link" to="/admin/settings">
        <span>SYSTEM SETTINGS</span>
        <strong>系统设置</strong>
      </RouterLink>
    </div>
    <nav>
      <RouterLink to="/admin/settings/database">数据库配置</RouterLink>
      <RouterLink to="/admin/settings/report-design">报表设计</RouterLink>
      <RouterLink to="/admin/settings/automation">报表自动化</RouterLink>
      <RouterLink to="/admin/settings/admins">管理员管理</RouterLink>
      <RouterLink to="/admin/settings/model-usage">模型用量</RouterLink>
    </nav>
  </section>

  <RouterView />
</template>

<style scoped>
.settings-navigation { margin-bottom:12px; }
.settings-home-link { height:100%; display:flex; flex-direction:column; justify-content:center; color:inherit; text-decoration:none; }
.settings-home-link:focus-visible { outline:2px solid var(--cyan); outline-offset:4px; }
</style>
