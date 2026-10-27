import { getDevices } from '../api/deviceContext.ts'

// Display labels come from the catalog; identifiers and download paths stay unchanged.
export function formatDeviceDisplay(value: unknown) {
  const names = new Map<string, string>([['cutter01', '20KW切割机'], ['cutter02', '30KW切割机'],
    ...getDevices().filter((item) => item.machine_no).map((item): [string, string] => [item.machine_no?.toLowerCase() || '', item.name])])
  return String(value ?? '').replace(/(^|[^a-z0-9])(Cutter\d+)(?=$|[^a-z0-9])/gi,
    (match, prefix, id) => names.has(id.toLowerCase()) ? `${prefix}${names.get(id.toLowerCase())}` : match)
}
