const INSTRUCTION_TYPES = Object.freeze({
  P: '拼板',
  S: '结构',
  W: '外板',
  L: '立材',
  Z: 'Z',
})

// Match the parameter prefix; product codes and trailing sequence/letters do not
// determine whether material and thickness can be filled automatically.
const STANDARD_NAME = /^([PSWLZ])([A-Z0-9]*?[A-Z])(\d{3})(.{2})(\d{2})?/iu

export function parseCuttingInstructionFilename(value) {
  const filename = String(value || '').trim().split(/[\\/]/).pop() || ''
  const extensionIndex = filename.lastIndexOf('.')
  if (extensionIndex >= 0 && !/\.(GEN|TXT)$/i.test(filename)) return null
  const stem = extensionIndex >= 0 ? filename.slice(0, extensionIndex) : filename
  const match = STANDARD_NAME.exec(stem)
  if (!match) return null

  const instructionType = match[1].toUpperCase()
  const materialCode = match[4]
  const thickness = Number(match[3]) / 10
  if (!Number.isFinite(thickness) || thickness <= 0) return null

  return {
    instructionType,
    instructionTypeLabel: INSTRUCTION_TYPES[instructionType],
    intermediateProduct: match[2].toUpperCase(),
    thickness,
    materialCode,
    material: materialCode,
    sequence: match[5] || '',
  }
}
