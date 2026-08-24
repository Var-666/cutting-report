<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <section class="report-navigation" aria-label="报表导航">
    <div><span>REPORTS</span><strong>报表</strong></div>
    <nav>
      <RouterLink to="/admin/reports/leadership">领导报表</RouterLink>
      <RouterLink to="/admin/reports/workshop">车间报表</RouterLink>
      <RouterLink to="/admin/reports/equipment">设备报表</RouterLink>
      <RouterLink to="/admin/reports/center">生成与历史</RouterLink>
    </nav>
  </section>
  <RouterView />
</template>
