import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deviceSwitchQuery } from '../api/deviceContext.js'
import { changeDevice, deviceSelection, loadDevices } from '../state/devices.js'

export function useDeviceSelection() {
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
  return { deviceSelection, switching, switchDevice, loadDevices }
}
