import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { deviceSwitchQuery } from '../api/deviceContext.ts'
import { changeDevice, deviceSelection, loadDevices } from '../state/devices.ts'

export function useDeviceSelection() {
  const route = useRoute()
  const router = useRouter()
  const switching = ref(false)
  async function switchDevice(id: string) {
    if (switching.value || id === deviceSelection.id) return
    switching.value = true
    try {
      const previous = deviceSelection.id
      if ((id === 'welding') !== (previous === 'welding')) {
        changeDevice(id)
        try {
          const failure = await router.replace(id === 'welding' ? '/admin/welding/home' : '/admin/home')
          if (failure) changeDevice(previous)
        }
        catch (error) { changeDevice(previous); throw error }
        return
      }
      const query = deviceSwitchQuery(route.name)
      if (query) await router.replace({ query: { ...route.query, ...query } })
      changeDevice(id)
    } finally { switching.value = false }
  }
  onMounted(loadDevices)
  return { deviceSelection, switching, switchDevice, loadDevices }
}
