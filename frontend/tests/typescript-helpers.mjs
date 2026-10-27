import ts from 'typescript'
import vm from 'node:vm'
import { toError } from '../src/utils/errors.ts'

// Fixtures execute the same source as before; erase annotations before evaluating it.
export function javascript(source) {
  return ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ESNext, module: ts.ModuleKind.ESNext } }).outputText
}
export function scriptFunction(...args) {
  const body = args.pop()
  // CommonJS namespaces on newer Node.js include the non-identifier export module.exports.
  const indexes = args.flatMap((name, index) => /^[A-Za-z_$][\w$]*$/.test(name) ? [index] : [])
  const fn = new Function('toError', 'withDefaults', ...indexes.map(index => args[index]), javascript(body))
  return (...values) => fn(toError, (props, defaults) => {
    for (const [key, value] of Object.entries(defaults)) if (props[key] === undefined) props[key] = value
    return props
  }, ...indexes.map(index => values[index]))
}
export function runTypeScript(source, context) {
  context.toError ??= toError
  return vm.runInContext(javascript(source), context)
}
