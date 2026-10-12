const INSTRUCTION_TYPES = Object.freeze({
  P: '拼板',
  S: '结构',
  W: '外板',
  L: '立材',
})

const MATERIALS = Object.freeze({
  A0: 'A',
  A2: 'AH32',
  A6: 'AH36',
  SS: '双相不锈钢',
})

// Match the parameter prefix; product codes and trailing sequence/letters do not
// determine whether material and thickness can be filled automatically.
const STANDARD_NAME = /^([PSWL])([A-Z0-9]*?[A-Z])(\d{3})(A0|A2|A6|SS)(\d{2})?/i

export function parseCuttingInstructionFilename(value) {
  const filename = String(value || '').trim().split(/[\\/]/).pop() || ''
  const extensionIndex = filename.lastIndexOf('.')
  if (extensionIndex >= 0 && !/\.(GEN|TXT)$/i.test(filename)) return null
  const stem = extensionIndex >= 0 ? filename.slice(0, extensionIndex) : filename
  const match = STANDARD_NAME.exec(stem)
  if (!match) return null

  const instructionType = match[1].toUpperCase()
  const materialCode = match[4].toUpperCase()
  const thickness = Number(match[3]) / 10
  if (!Number.isFinite(thickness) || thickness <= 0) return null

  return {
    instructionType,
    instructionTypeLabel: INSTRUCTION_TYPES[instructionType],
    intermediateProduct: match[2].toUpperCase(),
    thickness,
    materialCode,
    material: MATERIALS[materialCode],
    sequence: match[5] || '',
  }
}
