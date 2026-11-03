<script setup lang="ts">
import { ref } from 'vue'
import { session } from '../state/session'
import WorkshopView from './WorkshopView.vue'
import MyConfirmedTasksPanel from '../components/workshop/MyConfirmedTasksPanel.vue'

const myTasks = ref<InstanceType<typeof MyConfirmedTasksPanel> | null>(null)
</script>

<template>
  <div v-if="session.user" class="worker-tasks-layout">
    <WorkshopView :user="session.user" mode="worker" @refreshed="myTasks?.refresh()" />
    <MyConfirmedTasksPanel ref="myTasks" />
  </div>
</template>

<style scoped>
.worker-tasks-layout { display:grid; grid-template-columns:minmax(0,1fr) 340px; align-items:start; gap:16px; min-width:0; }
.worker-tasks-layout > * { min-width:0; }
@media(max-width:1699px) { .worker-tasks-layout { grid-template-columns:minmax(0,1fr); } }
</style>
