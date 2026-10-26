import { getDevices } from '../api/deviceContext.js'

// Display labels come from the catalog; identifiers and download paths stay unchanged.
export function formatDeviceDisplay(value) {
  const names = new Map([['cutter01', '20KW切割机'], ['cutter02', '30KW切割机'],
    ...getDevices().filter((item) => item.machine_no).map((item) => [item.machine_no.toLowerCase(), item.name])])
  return String(value ?? '').replace(/(^|[^a-z0-9])(Cutter\d+)(?=$|[^a-z0-9])/gi,
    (match, prefix, id) => names.has(id.toLowerCase()) ? `${prefix}${names.get(id.toLowerCase())}` : match)
}
