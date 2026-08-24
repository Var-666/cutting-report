<script setup>
import { session } from '../state/session'
import WorkshopView from './WorkshopView.vue'
</script>

<template>
  <WorkshopView :user="session.user" mode="worker" />
</template>
