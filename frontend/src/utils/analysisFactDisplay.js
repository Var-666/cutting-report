export function compactAnalysisFactValue(value) {
  return String(value ?? '').split(/[，；]/, 1)[0].trim()
}

export function compactAnalysisFactLabel(label, value) {
  const text = String(label ?? '')
  return /[，；]/.test(String(value ?? '')) ? text.split('与', 1)[0].trim() : text
}
