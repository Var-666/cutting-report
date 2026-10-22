import { reactive } from 'vue'
import { getDeviceCatalog } from '../api/reports.js'
import { getDeviceId, getDevices, selectDevice, setDeviceCatalog, subscribeDevices } from '../api/deviceContext.js'
import { stopReportPreload } from './reportPreload.js'

export const deviceSelection = reactive({ id: getDeviceId(), items: getDevices(), ready: false, loading: false, error: '' })
let loading = null
let generation = 0
subscribeDevices(() => {
  deviceSelection.id = getDeviceId()
  deviceSelection.items = getDevices()
  if (!deviceSelection.items.length) {
    generation += 1
    loading = null
    deviceSelection.ready = false
    deviceSelection.loading = false
    deviceSelection.error = ''
  }
})
export async function loadDevices() {
  if (deviceSelection.ready) return
  if (loading) return loading
  deviceSelection.loading = true
  deviceSelection.error = ''
  const run = generation
  loading = getDeviceCatalog().then((data) => {
    if (run !== generation) return
    setDeviceCatalog(data)
    deviceSelection.ready = true
  }).catch((error) => {
    if (run !== generation) return
    deviceSelection.error = error.message
  }).finally(() => {
    if (run === generation) { deviceSelection.loading = false; loading = null }
  })
  return loading
}
export function changeDevice(id) {
  if (id === deviceSelection.id) return
  // End preload chains and abort shared reads before pages remount for the new scope.
  stopReportPreload()
  selectDevice(id)
}
