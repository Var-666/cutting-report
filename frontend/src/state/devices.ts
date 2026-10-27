import { reactive } from 'vue'
import { getDeviceCatalog, getProductionLines } from '../api/devices.ts'
import { getDeviceId, getDevices, selectDevice, setDeviceCatalog, subscribeDevices } from '../api/deviceContext.ts'
import { stopReportPreload } from './reportPreload.ts'

export const deviceSelection = reactive({ id: getDeviceId(), items: getDevices(), ready: false, loading: false, error: '' })
let loading: Promise<void> | null = null
let generation = 0
let includesLines = false
subscribeDevices(() => {
  deviceSelection.id = getDeviceId()
  deviceSelection.items = getDevices()
  if (!deviceSelection.items.length) {
    generation += 1
    loading = null
    deviceSelection.ready = false
    deviceSelection.loading = false
    deviceSelection.error = ''
    includesLines = false
  }
})
export async function loadDevices({ force = false, includeLines = includesLines } = {}) {
  if (deviceSelection.ready && !force && (!includeLines || includesLines)) return
  if (loading) return loading
  deviceSelection.loading = true
  deviceSelection.error = ''
  const run = generation
  loading = (includeLines ? getProductionLines() : getDeviceCatalog()).then((data) => {
    if (run !== generation) return
    setDeviceCatalog(data)
    includesLines = includeLines
    deviceSelection.ready = true
  }).catch((error) => {
    if (run !== generation) return
    deviceSelection.error = error.message
  }).finally(() => {
    if (run === generation) { deviceSelection.loading = false; loading = null }
  })
  return loading
}
export function changeDevice(id: string) {
  if (id === deviceSelection.id) return
  // End preload chains and abort shared reads before pages remount for the new scope.
  stopReportPreload()
  selectDevice(id)
}
