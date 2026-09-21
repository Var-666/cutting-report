<script setup>
import { RouterLink } from 'vue-router'
import AppIcon from '../components/AppIcon.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

const modules = [
  { to: '/admin/settings/database', eyebrow: 'DATA SOURCES', title: '数据库配置', description: '检查生产库、工人 HR 库和切割文件库，测试连接并预览数据。', icon: 'database', tone: 'blue' },
  { to: '/admin/settings/report-design', eyebrow: 'REPORT DESIGN', title: '报表设计', description: '维护查询任务、字段映射、报表定义和输出模板。', icon: 'grid', tone: 'violet' },
  { to: '/admin/settings/automation', eyebrow: 'AUTOMATION', title: '报表自动化', description: '配置定时生成、邮件收件人、发送频率和失败重试。', icon: 'activity', tone: 'teal' },
  { to: '/admin/settings/admins', eyebrow: 'ACCESS CONTROL', title: '管理员管理', description: '从 HR 人员库授予管理员权限，并保留权限变更记录。', icon: 'user', tone: 'green' },
]
</script>

<template>
  <div class="management-page settings-home-page">
    <ManagementPageHero eyebrow="SYSTEM SETTINGS" title="系统设置" description="集中维护生产数据源、报表定义、自动发送和管理员权限，所有配置均保留清晰的安全边界。" icon="settings" status-label="配置中心" status-value="4 个管理模块" status-note="数据源、报表、自动化与权限" tone="teal" />
    <section class="settings-overview" aria-label="系统设置模块">
      <RouterLink v-for="item in modules" :key="item.to" :to="item.to" :class="`tone-${item.tone}`">
        <span class="settings-module-icon"><AppIcon :name="item.icon" :size="23" /></span>
        <small>{{ item.eyebrow }}</small><h2>{{ item.title }}</h2><p>{{ item.description }}</p><b>打开配置</b>
      </RouterLink>
    </section>
  </div>
</template>

<style scoped>
.settings-overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.settings-overview a{position:relative;min-height:184px;display:flex;flex-direction:column;padding:22px;color:var(--navy);border:1px solid #dbe6ec;border-radius:11px;background:#fff;box-shadow:0 8px 24px rgba(21,54,78,.06);text-decoration:none;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.settings-overview a::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;border-radius:11px 0 0 11px;background:#237fb7}.settings-overview a.tone-violet::before{background:#7367dd}.settings-overview a.tone-teal::before{background:#16a39d}.settings-overview a.tone-green::before{background:#2aa473}.settings-overview a:hover{transform:translateY(-2px);border-color:#bad3df;box-shadow:0 14px 30px rgba(21,54,78,.1)}.settings-module-icon{width:40px;height:40px;display:grid;place-items:center;margin-bottom:14px;border-radius:10px;color:#237fb7;background:#eaf5fb}.tone-violet .settings-module-icon{color:#6557c7;background:#efedff}.tone-teal .settings-module-icon{color:#14867f;background:#e8f7f5}.tone-green .settings-module-icon{color:#23825e;background:#eaf7f1}.settings-overview small{color:#4e829c;font-size:9px;font-weight:800;letter-spacing:1.35px}.settings-overview h2{margin:5px 0 8px;font-size:18px}.settings-overview p{margin:0;color:var(--muted);font-size:11px;line-height:1.75}.settings-overview b{margin-top:auto;padding-top:14px;color:var(--blue);font-size:10px}@media(max-width:760px){.settings-overview{grid-template-columns:1fr}.settings-overview a{min-height:170px}}
</style>
