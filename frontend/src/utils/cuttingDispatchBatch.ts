export function matchesDispatchParameters(current: { document_id?: string; version_id?: string | null; relative_path?: string; machine_ip?: string; material?: string; thickness?: number | string; quantity?: number | string } | null | undefined, item: { documentId: string; versionId?: string | null; filePath?: string; material?: string; thickness?: number | string; quantity?: number | string } | null | undefined, machineIp: string) {
  if (!current || !item) return false
  const normalizePath = (value: unknown) => String(value || '').replaceAll('\\', '/')
  return String(current.document_id) === item.documentId
    && String(current.version_id || '') === String(item.versionId || '')
    && normalizePath(current.relative_path) === normalizePath(item.filePath)
    && current.machine_ip === machineIp
    && String(current.material || '').trim().toLowerCase() === String(item.material || '').trim().toLowerCase()
    && Number(current.thickness) === Number(item.thickness)
    && Number(current.quantity) === Number(item.quantity)
}
