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

const STANDARD_NAME = /^([PSWL])(\d{3}C)(\d{3})(A0|A2|A6|SS)(0[1-9]|[1-9]\d)(?:\.(GEN|TXT))?$/i

export function parseCuttingInstructionFilename(value) {
  const filename = String(value || '').trim().split(/[\\/]/).pop() || ''
  const match = STANDARD_NAME.exec(filename)
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
    sequence: match[5],
  }
}
