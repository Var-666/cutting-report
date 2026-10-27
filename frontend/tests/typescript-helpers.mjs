import ts from 'typescript'
import vm from 'node:vm'
import { toError } from '../src/utils/errors.ts'

// Fixtures execute the same source as before; erase annotations before evaluating it.
export function javascript(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext } }).outputText
}
export function scriptFunction(...args) {
  const body = args.pop()
  const fn = new Function('toError', 'withDefaults', ...args, javascript(body))
  return (...values) => fn(toError, (props, defaults) => {
    for (const [key, value] of Object.entries(defaults)) if (props[key] === undefined) props[key] = value
    return props
  }, ...values)
}
export function runTypeScript(source, context) {
  context.toError ??= toError
  return vm.runInContext(javascript(source), context)
}
