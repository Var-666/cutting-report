import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'
import { test } from 'vitest'
import ts from 'typescript'
import { toError } from '../src/utils/errors.ts'

test('business contracts reject invalid roles, report types and email outcomes while matching the public line contract', () => {
  const fixture = fileURLToPath(new URL('./fixtures/type-contracts.ts', import.meta.url))
  const program = ts.createProgram([fixture], { strict: true, noEmit: true, skipLibCheck: true, allowImportingTsExtensions: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler })
  const diagnostics = ts.getPreEmitDiagnostics(program)
  assert.deepEqual(diagnostics.map(item => ts.flattenDiagnosticMessageText(item.messageText, '\n')), [])
})

test('caught errors retain cancellation and server retry metadata', () => {
  const failure = Object.assign(new Error('繁忙'), { status: 503, retryAfterMs: 1000 })
  assert.equal(toError(failure), failure)
  assert.equal(toError(new DOMException('取消', 'AbortError')).name, 'AbortError')
  assert.equal(toError('失败').message, '失败')
})

test('contract generation honors an explicit Python command and reports when it is unusable', () => {
  const directory = mkdtempSync(join(tmpdir(), 'cutting-contracts-test-'))
  try {
    const python = join(directory, 'missing-python')
    const script = fileURLToPath(new URL('../scripts/generate-api.mjs', import.meta.url))
    const result = spawnSync(process.execPath, [script, '--check'], {
      env: { ...process.env, PYTHON: python }, encoding: 'utf8',
    })
    assert.equal(result.status, 1)
    assert.ok(result.stderr.includes('ENOENT'))
    assert.ok(result.stderr.includes(python))
  } finally {
    rmSync(directory, { recursive: true })
  }
})
