<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <section class="settings-header">
    <div>
      <span class="eyebrow">SYSTEM SETTINGS</span>
      <h1>系统设置</h1>
      <p>集中管理数据源、报表定义和自动化任务。</p>
    </div>
    <RouterLink class="settings-back" to="/admin/settings">设置概览</RouterLink>
  </section>

  <nav class="settings-tabs" aria-label="系统设置导航">
    <RouterLink to="/admin/settings/database">数据库配置</RouterLink>
    <RouterLink to="/admin/settings/report-design">报表设计</RouterLink>
    <RouterLink to="/admin/settings/automation">报表自动化</RouterLink>
  </nav>

  <RouterView />
</template>

<style scoped>
.settings-header { min-height:150px; display:flex; align-items:center; justify-content:space-between; gap:24px; }
.settings-header h1 { margin:7px 0 6px; color:var(--navy); font-size:32px; }
.settings-header p { margin:0; color:var(--muted); }
.settings-back { padding:10px 14px; color:var(--blue); border:1px solid #c8d8e1; background:#fff; text-decoration:none; font-size:11px; font-weight:700; }
.settings-tabs { display:flex; gap:4px; margin-bottom:22px; padding:5px; border:1px solid var(--line); background:#fff; box-shadow:0 7px 22px rgba(21,54,78,.045); }
.settings-tabs a { min-height:42px; display:flex; align-items:center; padding:0 20px; color:#536b7a; text-decoration:none; font-size:12px; font-weight:700; }
.settings-tabs a.router-link-active { color:#fff; background:var(--blue); }
@media (max-width:700px) { .settings-header { align-items:flex-start; flex-direction:column; justify-content:center; padding:24px 0; }.settings-back { display:none; }.settings-tabs { overflow-x:auto; }.settings-tabs a { flex:0 0 auto; } }
</style>
