import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import ts from 'typescript'
import { parse } from '@vue/compiler-sfc'
import { scriptFunction } from './typescript-helpers.mjs'
import { toError } from '../src/utils/errors.ts'

const sourceRoot = new URL('../src/', import.meta.url)
function files(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = new URL(entry.name + (entry.isDirectory() ? '/' : ''), directory)
    return entry.isDirectory() ? files(path) : [path]
  })
}

test('maintained source uses TypeScript with strict checking and no blanket suppressions', () => {
  for (const path of files(sourceRoot)) {
    assert.ok(!path.pathname.endsWith('.js'), path.pathname)
    if (!/\.(?:vue|ts)$/.test(path.pathname)) continue
    const source = readFileSync(path, 'utf8')
    if (path.pathname.endsWith('.vue')) {
      const { descriptor } = parse(source)
      for (const script of [descriptor.script, descriptor.scriptSetup].filter(Boolean)) assert.equal(script.lang, 'ts', path.pathname)
    }
    if (!path.pathname.includes('/vendor/')) assert.doesNotMatch(source, /@(?:ts|vue)-(?:ignore|nocheck|expect-error)/, path.pathname)
  }
  const config = JSON.parse(readFileSync(new URL('../tsconfig.json', import.meta.url), 'utf8'))
  assert.equal(config.compilerOptions.strict, true)
  assert.notEqual(config.compilerOptions.allowJs, true)
})

test('business contracts reject invalid roles, line capabilities, report types and email outcomes', () => {
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

test('production builds require type checking and the frontend suite before bundling', () => {
  const { scripts } = JSON.parse(readFileSync(new URL('../package.json', import.meta.url), 'utf8'))
  const steps = scripts.build.split(' && ')
  assert.deepEqual(steps.slice(0, 2), ['pnpm run typecheck', 'pnpm test'])
  assert.ok(steps.indexOf('vite build') > 1)
})

test('script fixtures ignore non-identifier module exports without shifting bindings', () => {
  const run = scriptFunction('first', 'module.exports', 'last', 'return [first, last]')
  assert.deepEqual(run(1, {}, 3), [1, 3])
})
