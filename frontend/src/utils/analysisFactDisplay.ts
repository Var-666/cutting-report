export function compactAnalysisFactValue(value: unknown) {
  return String(value ?? '').split(/[，；]/, 1)[0].trim()
}

export function compactAnalysisFactLabel(label: unknown, value: unknown) {
  const text = String(label ?? '')
  return /[，；]/.test(String(value ?? '')) ? text.split('与', 1)[0].trim() : text
}
