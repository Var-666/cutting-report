import type { LocationQueryRaw, RouteRecordName } from 'vue-router'
import type { DeviceCatalog, ProductionLine } from '../types/devices'
// Only device identifiers and public labels live here; production data stays in reportCache.
const storageKey = 'cutting-report.device-id'
function storedDeviceId() {
  try { return globalThis.sessionStorage?.getItem(storageKey) || '' }
  catch { return '' }
}
function rememberDevice(id: string | null) {
  try {
    if (id) globalThis.sessionStorage?.setItem(storageKey, id)
    else globalThis.sessionStorage?.removeItem(storageKey)
  } catch { /* Storage restrictions must not prevent device queries or switching. */ }
}
let rememberedId = storedDeviceId()
let defaultDeviceId = ''
let selectedId = rememberedId || defaultDeviceId
let catalog: ProductionLine[] = []
const subscribers = new Set<() => void>()
const switchQueries = new Map<RouteRecordName | null | undefined, () => LocationQueryRaw>()
export const getDeviceId = () => selectedId
export const getDevices = () => catalog
export const subscribeDevices = (listener: () => void) => { subscribers.add(listener); return () => subscribers.delete(listener) }
function notify() { for (const listener of subscribers) listener() }
export function setDeviceCatalog(data: DeviceCatalog) {
  catalog = data.items
  defaultDeviceId = data.default_device_id
  selectedId = catalog.some((item) => item.id === rememberedId) ? rememberedId : defaultDeviceId
  rememberedId = selectedId
  rememberDevice(selectedId)
  notify()
}
export function selectDevice(id: string) {
  if (!catalog.some((item) => item.id === id)) throw new Error('设备不存在或尚未接入')
  selectedId = id
  rememberedId = id
  rememberDevice(id)
  notify()
}
export function resetDeviceContext() { rememberDevice(null); rememberedId = ''; defaultDeviceId = ''; selectedId = defaultDeviceId; catalog = []; switchQueries.clear(); notify() }
export function deviceUrl(url: string, deviceId = selectedId) {
  if (!/^\/api\/v1\/(workshop|leadership-report|equipment-report|energy-report|analysis|options|projects|ships|reports|conditions|alarms)(?:[/?]|$)|^\/api\/v1\/database\/preview(?:[?]|$)|^\/api\/v1\/designer\/query-tasks\/[^/?]+\/preview(?:[?]|$)/.test(url)) return url
  const [path, query = ''] = url.split('?')
  const values = new URLSearchParams(query)
  if (!values.has('device_id')) {
    if (!deviceId) throw new Error('设备目录尚未加载，请稍后重试')
    values.set('device_id', deviceId)
  }
  return `${path}?${values}`
}

export function registerDeviceSwitchQuery(routeName: RouteRecordName | null | undefined, reader: () => LocationQueryRaw) {
  switchQueries.set(routeName, reader)
  return () => { if (switchQueries.get(routeName) === reader) switchQueries.delete(routeName) }
}
export function deviceSwitchQuery(routeName: RouteRecordName | null | undefined) { return switchQueries.get(routeName)?.() }
