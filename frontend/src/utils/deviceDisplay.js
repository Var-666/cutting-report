// Display aliases only: source identifiers and download paths stay unchanged.
export function formatDeviceDisplay(value) {
  return String(value ?? '').replace(
    /(^|[^a-z0-9])Cutter01(?=$|[^a-z0-9])/gi,
    (_, prefix) => `${prefix}20KW切割机`,
  )
}
