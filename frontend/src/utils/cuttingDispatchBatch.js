export function matchesDispatchParameters(current, item, machineIp) {
  if (!current || !item) return false
  const normalizePath = (value) => String(value || '').replaceAll('\\', '/')
  return String(current.document_id) === item.documentId
    && String(current.version_id || '') === String(item.versionId || '')
    && normalizePath(current.relative_path) === normalizePath(item.filePath)
    && current.machine_ip === machineIp
    && String(current.material || '').trim().toLowerCase() === String(item.material || '').trim().toLowerCase()
    && Number(current.thickness) === Number(item.thickness)
    && Number(current.quantity) === Number(item.quantity)
}
