<script setup>
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <section class="report-navigation" aria-label="文件派发目录">
    <div><span>CUTTING FILES</span><strong>文件派发</strong></div>
    <nav aria-label="文件派发子导航">
      <RouterLink to="/admin/cutting-dispatch/downloads">文件下载</RouterLink>
      <RouterLink to="/admin/cutting-dispatch/push">文件推送</RouterLink>
    </nav>
  </section>
  <RouterView v-slot="{ Component, route }">
    <KeepAlive :max="2">
      <component :is="Component" :key="route.name" />
    </KeepAlive>
  </RouterView>
</template>
