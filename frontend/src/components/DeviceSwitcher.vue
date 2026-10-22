<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deviceSwitchQuery } from '../api/deviceContext.js'
import AppSelect from './AppSelect.vue'
import AppIcon from './AppIcon.vue'
import { changeDevice, deviceSelection, loadDevices } from '../state/devices.js'

const route = useRoute()
const router = useRouter()
const switching = ref(false)
async function switchDevice(id) {
  if (switching.value || id === deviceSelection.id) return
  switching.value = true
  try {
    const query = deviceSwitchQuery(route.name)
    if (query) await router.replace({ query: { ...route.query, ...query } })
    changeDevice(id)
  } finally { switching.value = false }
}
onMounted(loadDevices)
</script>

<template>
  <section class="device-switcher" aria-label="当前设备">
    <label v-if="deviceSelection.ready" class="device-switcher__field">
      <AppIcon class="device-switcher__icon" name="factory" :size="16" />
      <AppSelect :model-value="deviceSelection.id" name="current_device" aria-label="切换当前设备" :disabled="switching" @update:model-value="switchDevice">
        <option v-for="item in deviceSelection.items" :key="item.id" :value="item.id">{{ item.name }}</option>
      </AppSelect>
    </label>
    <span v-else-if="deviceSelection.loading" class="device-switcher__loading" role="status"><AppIcon name="factory" :size="16" />正在读取设备…</span>
    <div v-else-if="deviceSelection.error" class="device-switcher__error" role="alert" :title="deviceSelection.error">
      <span>设备读取失败</span><button type="button" @click="loadDevices">重试</button>
    </div>
  </section>
</template>

<style scoped>
.device-switcher{display:flex;align-items:center;flex:0 0 auto;width:176px;color:#e3f4f8;font-size:12px}
.device-switcher__field{position:relative;display:block;width:100%;margin:0}
.device-switcher__icon{position:absolute;top:50%;left:12px;z-index:1;color:#83d7df;transform:translateY(-50%);pointer-events:none}
.device-switcher__field :deep(.app-select){height:36px}
.device-switcher__field :deep(.app-select__trigger){padding:0 12px 0 38px;color:#f0faff;border:1px solid #46778a;border-radius:8px;background:rgba(255,255,255,.07);font-size:12px;font-weight:600}
.device-switcher__field :deep(.app-select__trigger:hover){border-color:#7ec5d4;background:rgba(255,255,255,.12)}
.device-switcher__field :deep(.app-select.is-open .app-select__trigger),.device-switcher__field :deep(.app-select__trigger:focus-visible){border-color:#8ee4ee;box-shadow:0 0 0 3px rgba(142,228,238,.14)}
.device-switcher__field :deep(.app-select__trigger i){width:6px;height:6px;flex-basis:6px;color:#a5d7e3}
.device-switcher__loading{display:flex;align-items:center;gap:8px;min-height:36px;color:#b9dce7;white-space:nowrap}
.device-switcher__error{display:flex;align-items:center;gap:8px;min-height:36px;color:#ffdcaf;white-space:nowrap}
.device-switcher__error button{min-height:30px;padding:0 8px;border-color:#b69a73;background:transparent;color:#ffe7c8;font-size:11px}
@media(max-width:600px){.device-switcher{width:152px}.device-switcher__field :deep(.app-select){height:36px}.device-switcher__field :deep(.app-select__trigger){padding-right:10px;font-size:12px}}
</style>
