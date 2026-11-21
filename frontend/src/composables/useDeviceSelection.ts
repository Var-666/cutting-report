import { reportAsyncError } from '../api/browserObservations.ts'
import { toError } from '../utils/errors.ts'
import { lineHome, selectedLineType } from '../utils/productionLines.ts'
import { navigationSection } from '../router/menus.ts'
import { onMounted, ref } from 'vue'
import { isNavigationFailure, NavigationFailureType, useRoute, useRouter } from 'vue-router'
import { allowDeviceSwitch, deviceSwitchQuery } from '../api/deviceContext.ts'
import { changeDevice, deviceSelection, loadDevices } from '../state/devices.ts'

export function useDeviceSelection() {
  const route = useRoute()
  const router = useRouter()
  const switching = ref(false)
  async function switchDevice(id: string, enter = false) {
    if (switching.value || (id === deviceSelection.id && !enter)) return
    switching.value = true
    try {
      if (!await allowDeviceSwitch(route.name)) return
      const previous = deviceSelection.id
      if (enter || selectedLineType(deviceSelection.items, id) !== selectedLineType(deviceSelection.items, previous)) {
        changeDevice(id)
        try {
          const failure = await router.replace(lineHome(deviceSelection.items, id))
          if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) changeDevice(previous)
        }
        catch (error) { changeDevice(previous); throw toError(error) }
        return
      }
      const query = navigationSection(route.path) === 'line' ? deviceSwitchQuery(route.name) : null
      if (query) {
        const failure = await router.replace({ query: { ...route.query, ...query } })
        if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) return
      }
      changeDevice(id)
    } finally { switching.value = false }
  }
  onMounted(() => { if (!deviceSelection.error) loadDevices().catch(reportAsyncError) })
  return { deviceSelection, switching, switchDevice, enterLine: (id: string) => switchDevice(id, true), loadDevices }
}
