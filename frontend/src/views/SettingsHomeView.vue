<script setup>
import { RouterLink } from 'vue-router'
</script>

<template>
  <section class="settings-overview">
    <RouterLink to="/admin/settings/database">
      <span>01</span><small>DATA SOURCES</small><h2>数据库配置</h2>
      <p>检查生产库、工人 HR 库和切割文件库，测试连接并预览数据。</p><b>进入设置 →</b>
    </RouterLink>
    <RouterLink to="/admin/settings/report-design">
      <span>02</span><small>REPORT DESIGN</small><h2>报表设计</h2>
      <p>维护查询任务、字段映射、报表定义和输出模板。</p><b>进入设置 →</b>
    </RouterLink>
    <RouterLink to="/admin/settings/automation">
      <span>03</span><small>AUTOMATION</small><h2>报表自动化</h2>
      <p>配置定时生成、邮件收件人、发送频率和失败重试。</p><b>进入设置 →</b>
    </RouterLink>
    <RouterLink to="/admin/settings/admins">
      <span>04</span><small>ACCESS CONTROL</small><h2>管理员管理</h2>
      <p>从 HR 人员库授予管理员权限，并保留权限变更记录。</p><b>进入设置 →</b>
    </RouterLink>
  </section>
</template>

<style scoped>
.settings-overview { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:18px; }
.settings-overview a { position:relative; min-height:245px; display:flex; flex-direction:column; padding:25px; color:var(--navy); border:1px solid var(--line); border-top:4px solid var(--cyan); background:#fff; box-shadow:0 10px 28px rgba(21,54,78,.065); text-decoration:none; transition:transform .18s ease,box-shadow .18s ease; }
.settings-overview a:hover { transform:translateY(-3px); box-shadow:0 16px 34px rgba(21,54,78,.11); }
.settings-overview span { position:absolute; top:16px; right:20px; color:#e0e9ed; font:800 42px/1 "Microsoft YaHei","PingFang SC",sans-serif; }
.settings-overview small { color:#168397; font-size:9px; font-weight:800; letter-spacing:1.8px; }
.settings-overview h2 { margin:18px 0 10px; font-size:21px; }
.settings-overview p { margin:0; color:var(--muted); font-size:12px; line-height:1.8; }
.settings-overview b { margin-top:auto; color:var(--blue); font-size:11px; }
@media (max-width:900px) { .settings-overview { grid-template-columns:1fr; }.settings-overview a { min-height:190px; } }
</style>
