<script setup>
import { RouterView } from 'vue-router'
</script>
<template><RouterView v-slot="{ Component, route }"><component :is="Component" :key="route.name" /></RouterView></template>
