<script setup lang="ts">
import { session } from '../state/session'
import WorkshopView from './WorkshopView.vue'
</script>

<template>
  <WorkshopView v-if="session.user" :user="session.user" mode="worker" />
</template>
