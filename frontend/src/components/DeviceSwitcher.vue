<script setup>
import AppSelect from './AppSelect.vue'
import AppIcon from './AppIcon.vue'
import { useDeviceSelection } from '../composables/useDeviceSelection.js'

const { deviceSelection, switching, switchDevice, loadDevices } = useDeviceSelection()
</script>

<template>
  <section class="device-switcher" aria-label="当前设备">
    <label v-if="deviceSelection.ready" class="device-switcher__field">
      <AppIcon class="device-switcher__icon" name="factory" :size="16" tone="current" />
      <AppSelect :model-value="deviceSelection.id" name="current_device" aria-label="切换当前设备" :disabled="switching" @update:model-value="switchDevice">
        <option v-for="item in deviceSelection.items" :key="item.id" :value="item.id">{{ item.name }}</option>
      </AppSelect>
    </label>
    <span v-else-if="deviceSelection.loading" class="device-switcher__loading" role="status"><AppIcon name="factory" :size="16" tone="current" />正在读取设备…</span>
    <div v-else-if="deviceSelection.error" class="device-switcher__error" role="alert" :title="deviceSelection.error">
      <span>设备读取失败</span><button type="button" @click="loadDevices">重试</button>
    </div>
  </section>
</template>

<style scoped>
.device-switcher{display:flex;align-items:center;width:190px;min-width:0;color:rgb(var(--primary-color));font-size:13px}
.device-switcher__field{display:flex;align-items:center;gap:8px;width:100%;margin:0}
.device-switcher__icon{flex:0 0 auto}
.device-switcher__loading,.device-switcher__error{display:flex;align-items:center;gap:8px;min-height:34px;font-size:12px}
.device-switcher__loading{color:var(--text-color-3)}.device-switcher__error{color:rgb(var(--warning-color))}.device-switcher__error button{min-height:28px;padding:0 8px;font-size:12px}
@media(max-width:600px){.device-switcher{width:176px}}
</style>
