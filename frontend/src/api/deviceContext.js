// Only device identifiers and public labels live here; production data stays in reportCache.
const storageKey = 'cutting-report.device-id'
function storedDeviceId() {
  try { return globalThis.sessionStorage?.getItem(storageKey) || '' }
  catch { return '' }
}
function rememberDevice(id) {
  try {
    if (id) globalThis.sessionStorage?.setItem(storageKey, id)
    else globalThis.sessionStorage?.removeItem(storageKey)
  } catch { /* Storage restrictions must not prevent device queries or switching. */ }
}
let rememberedId = storedDeviceId()
let defaultDeviceId = ''
let selectedId = rememberedId || defaultDeviceId
let catalog = []
const subscribers = new Set()
const switchQueries = new Map()
export const getDeviceId = () => selectedId
export const getDevices = () => catalog
export const subscribeDevices = (listener) => { subscribers.add(listener); return () => subscribers.delete(listener) }
function notify() { for (const listener of subscribers) listener() }
export function setDeviceCatalog(data) {
  catalog = data.items
  defaultDeviceId = data.default_device_id
  selectedId = catalog.some((item) => item.id === rememberedId) ? rememberedId : defaultDeviceId
  rememberedId = selectedId
  rememberDevice(selectedId)
  notify()
}
export function selectDevice(id) {
  if (!catalog.some((item) => item.id === id)) throw new Error('设备不存在或尚未接入')
  selectedId = id
  rememberedId = id
  rememberDevice(id)
  notify()
}
export function resetDeviceContext() { rememberDevice(null); rememberedId = ''; defaultDeviceId = ''; selectedId = defaultDeviceId; catalog = []; switchQueries.clear(); notify() }
export function deviceUrl(url, deviceId = selectedId) {
  if (!/^\/api\/v1\/(workshop|leadership-report|equipment-report|energy-report|analysis|options|projects|ships|reports|conditions|alarms)(?:[/?]|$)|^\/api\/v1\/database\/preview(?:[?]|$)|^\/api\/v1\/designer\/query-tasks\/[^/?]+\/preview(?:[?]|$)/.test(url)) return url
  const [path, query = ''] = url.split('?')
  const values = new URLSearchParams(query)
  if (!values.has('device_id')) {
    if (!deviceId) throw new Error('设备目录尚未加载，请稍后重试')
    values.set('device_id', deviceId)
  }
  return values.size ? `${path}?${values}` : path
}

export function registerDeviceSwitchQuery(routeName, reader) {
  switchQueries.set(routeName, reader)
  return () => { if (switchQueries.get(routeName) === reader) switchQueries.delete(routeName) }
}
export function deviceSwitchQuery(routeName) { return switchQueries.get(routeName)?.() }
