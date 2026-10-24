<script setup>
import { RouterLink } from 'vue-router'
import projectLogo from '../../../assets/project-logo.png'
defineProps({ showTitle: { type: Boolean, default: true } })
</script>
<template>
  <RouterLink class="soybean-brand" to="/admin/home" aria-label="返回系统首页">
    <img class="brand-logo" :src="projectLogo" alt="" width="36" height="36" />
    <strong v-if="showTitle">切割生产智能产报</strong>
  </RouterLink>
</template>
