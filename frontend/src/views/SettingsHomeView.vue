<script setup>
import { onMounted, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { getLlmUsage } from '../api/reports.js'
import AppIcon from '../components/AppIcon.vue'
import ManagementPageHero from '../components/ManagementPageHero.vue'

const usage = ref(null)
const usageError = ref('')
const numberFormat = new Intl.NumberFormat('zh-CN')

async function loadUsage() {
  usageError.value = ''
  try {
    usage.value = await getLlmUsage()
  } catch (error) {
    usageError.value = error.message || '统计读取失败'
  }
}

onMounted(loadUsage)

const modules = [
  { to: '/admin/settings/database', eyebrow: 'DATA SOURCES', title: '数据库配置', description: '检查生产库、工人 HR 库和切割文件库，测试连接并预览数据。', icon: 'database', tone: 'blue' },
  { to: '/admin/settings/report-design', eyebrow: 'REPORT DESIGN', title: '报表设计', description: '维护查询任务、字段映射、报表定义和输出模板。', icon: 'grid', tone: 'violet' },
  { to: '/admin/settings/automation', eyebrow: 'AUTOMATION', title: '报表自动化', description: '配置定时生成、邮件收件人、发送频率和失败重试。', icon: 'activity', tone: 'teal' },
  { to: '/admin/settings/admins', eyebrow: 'ACCESS CONTROL', title: '管理员管理', description: '从 HR 人员库授予管理员权限，并保留权限变更记录。', icon: 'user', tone: 'green' },
  { to: '/admin/settings/model-usage', eyebrow: 'MODEL USAGE', title: '模型用量', description: '查看每日、每月 token 消耗趋势，按业务场景和模型汇总使用情况。', icon: 'sparkles', tone: 'violet' },
]
</script>

<template>
  <div class="management-page settings-home-page">
    <ManagementPageHero eyebrow="SYSTEM SETTINGS" title="系统设置" description="集中维护生产数据源、报表定义、自动发送、管理员权限和模型用量。" icon="settings" status-label="配置中心" status-value="5 个管理模块" status-note="数据源、报表、自动化、权限与用量" tone="teal" />
    <section class="llm-usage-card" aria-label="大模型使用统计">
      <div class="llm-usage-heading"><span class="llm-usage-icon"><AppIcon name="sparkles" :size="18" /></span><div><h2>大模型使用统计</h2><p>报表智能分析与告警智能分析 · 自启用统计起累计</p></div></div>
      <div v-if="usage" class="llm-usage-values">
        <div><span>调用次数</span><strong>{{ numberFormat.format(usage.call_count) }}</strong><small>次</small></div>
        <div><span>Token 消耗</span><strong>{{ usage.metered_count ? numberFormat.format(usage.total_tokens) : '—' }}</strong><small>tokens</small></div>
      </div>
      <div v-else-if="usageError" class="llm-usage-state" role="alert">{{ usageError }} <button type="button" @click="loadUsage">重试</button></div>
      <div v-else class="llm-usage-state" role="status">正在读取统计…</div>
      <p v-if="usage && usage.call_count > usage.metered_count" class="llm-usage-note">{{ numberFormat.format(usage.call_count - usage.metered_count) }} 次调用未返回 token 用量，合计仅包含已返回的用量。</p>
      <RouterLink to="/admin/settings/model-usage">查看每日 / 每月明细</RouterLink>
    </section>
    <section class="settings-overview" aria-label="系统设置模块">
      <RouterLink v-for="item in modules" :key="item.to" :to="item.to" :class="`tone-${item.tone}`">
        <span class="settings-module-icon"><AppIcon :name="item.icon" :size="23" /></span>
        <small>{{ item.eyebrow }}</small><h2>{{ item.title }}</h2><p>{{ item.description }}</p><b>打开配置</b>
      </RouterLink>
    </section>
  </div>
</template>

<style scoped>
.llm-usage-card{display:flex;align-items:center;flex-wrap:wrap;gap:12px 24px;margin:0;padding:18px 22px;border:1px solid #dbe6ec;border-radius:11px;background:#fff;box-shadow:0 8px 24px rgba(21,54,78,.06)}.llm-usage-heading{display:flex;align-items:center;gap:12px;min-width:250px;flex:1 1 300px}.llm-usage-icon{width:38px;height:38px;display:grid;place-items:center;flex:0 0 auto;border-radius:9px;color:#6557c7;background:#efedff}.llm-usage-heading h2{margin:0;color:var(--navy);font-size:15px}.llm-usage-heading p{margin:4px 0 0;color:var(--muted);font-size:10px;line-height:1.5}.llm-usage-values{display:flex;flex-wrap:wrap;gap:18px 34px}.llm-usage-values>div{min-width:116px}.llm-usage-values span{display:block;color:var(--muted);font-size:10px}.llm-usage-values strong{margin-right:5px;color:var(--navy);font-size:24px;font-variant-numeric:tabular-nums}.llm-usage-values small{color:var(--muted);font-size:10px}.llm-usage-note{flex-basis:100%;margin:0;color:#7e5b24;font-size:10px}.llm-usage-state{color:var(--muted);font-size:11px}.llm-usage-state button{margin-left:8px;border:0;background:none;color:var(--blue);font:inherit;text-decoration:underline;cursor:pointer}.llm-usage-state button:focus-visible{outline:2px solid var(--blue);outline-offset:3px}
.settings-overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.settings-overview a{position:relative;min-height:184px;display:flex;flex-direction:column;padding:22px;color:var(--navy);border:1px solid #dbe6ec;border-radius:11px;background:#fff;box-shadow:0 8px 24px rgba(21,54,78,.06);text-decoration:none;transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.settings-overview a::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;border-radius:11px 0 0 11px;background:#237fb7}.settings-overview a.tone-violet::before{background:#7367dd}.settings-overview a.tone-teal::before{background:#16a39d}.settings-overview a.tone-green::before{background:#2aa473}.settings-overview a:hover{transform:translateY(-2px);border-color:#bad3df;box-shadow:0 14px 30px rgba(21,54,78,.1)}.settings-module-icon{width:40px;height:40px;display:grid;place-items:center;margin-bottom:14px;border-radius:10px;color:#237fb7;background:#eaf5fb}.tone-violet .settings-module-icon{color:#6557c7;background:#efedff}.tone-teal .settings-module-icon{color:#14867f;background:#e8f7f5}.tone-green .settings-module-icon{color:#23825e;background:#eaf7f1}.settings-overview small{color:#4e829c;font-size:9px;font-weight:800;letter-spacing:1.35px}.settings-overview h2{margin:5px 0 8px;font-size:18px}.settings-overview p{margin:0;color:var(--muted);font-size:11px;line-height:1.75}.settings-overview b{margin-top:auto;padding-top:14px;color:var(--blue);font-size:10px}@media(max-width:760px){.settings-overview{grid-template-columns:1fr}.settings-overview a{min-height:170px}}
</style>
