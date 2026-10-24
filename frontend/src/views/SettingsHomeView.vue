<script setup>
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

const modules = [
  { to: '/admin/settings/database', eyebrow: 'DATA SOURCES', title: '数据库配置', icon: 'database', tone: 'blue' },
  { to: '/admin/settings/automation', eyebrow: 'AUTOMATION', title: '报表自动化', icon: 'activity', tone: 'teal' },
  { to: '/admin/settings/admins', eyebrow: 'ACCESS CONTROL', title: '管理员管理', icon: 'user', tone: 'green' },
  { to: '/admin/settings/model-usage', eyebrow: 'MODEL USAGE', title: '模型用量', icon: 'sparkles', tone: 'violet' },
]
</script>

<template>
  <div class="management-page settings-home-page">
    <ManagementPageHero eyebrow="SYSTEM SETTINGS" title="系统设置" icon="settings" status-label="配置中心" status-value="4 个管理模块" tone="teal" />
    <section class="settings-overview" aria-label="系统设置模块">
      <RouterLink v-for="item in modules" :key="item.to" :to="item.to" :class="`tone-${item.tone}`">
        <span class="settings-module-icon"><AppIcon :name="item.icon" :size="23" /></span>
        <small>{{ item.eyebrow }}</small><h2>{{ item.title }}</h2><b>打开配置</b>
      </RouterLink>
    </section>
  </div>
</template>

<style scoped>
.settings-overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.settings-overview a{position:relative;min-height:164px;display:flex;flex-direction:column;padding:22px;color:var(--navy);border:1px solid var(--theme-border-color);border-radius:11px;background:rgb(var(--container-bg-color));box-shadow:var(--theme-shadow);text-decoration:none;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.settings-overview a::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;border-radius:11px 0 0 11px;background:rgb(var(--info-color))}.settings-overview a.tone-violet::before{background:rgb(var(--primary-color))}.settings-overview a.tone-teal::before{background:rgb(var(--info-color))}.settings-overview a.tone-green::before{background:rgb(var(--success-color))}.settings-overview a:hover{transform:translateY(-2px);border-color:var(--theme-border-color);box-shadow:var(--theme-shadow)}.settings-module-icon{width:40px;height:40px;display:grid;place-items:center;margin-bottom:14px;border-radius:10px;color:rgb(var(--info-color));background:rgb(var(--info-color) / .1)}.tone-violet .settings-module-icon{color:var(--text-color-3);background:rgb(var(--primary-color) / .1)}.tone-teal .settings-module-icon{color:rgb(var(--info-color));background:rgb(var(--info-color) / .1)}.tone-green .settings-module-icon{color:rgb(var(--success-color));background:rgb(var(--success-color) / .1)}.settings-overview small{color:var(--text-color-3);font-size:9px;font-weight:800;letter-spacing:1.35px}.settings-overview h2{margin:5px 0 8px;font-size:18px}.settings-overview b{margin-top:auto;padding-top:14px;color:var(--blue);font-size:10px}@media(max-width:760px){.settings-overview{grid-template-columns:1fr}.settings-overview a{min-height:150px}}
</style>
