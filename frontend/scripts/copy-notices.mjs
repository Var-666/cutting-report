import assert from 'node:assert/strict'
import { cp, mkdir, readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { createRequire } from 'node:module'

const root = fileURLToPath(new URL('..', import.meta.url))
const { dependencies } = JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8'))
const echartsRequire = createRequire(createRequire(import.meta.url).resolve('echarts/package.json'))
const indirect = {
  zrender: dirname(echartsRequire.resolve('zrender/package.json')),
  tslib: dirname(echartsRequire.resolve('tslib')),
}
for (const name of [...Object.keys(dependencies), 'zrender', 'tslib', 'soybean-admin']) {
  const source = indirect[name] || resolve(root, name === 'soybean-admin' ? 'src/vendor/soybean-admin' : `node_modules/${name}`)
  const notices = (await readdir(source)).filter((file) => /^(LICENSE|COPYING|NOTICE)/i.test(file))
  assert.ok(notices.length, `Missing third-party notice: ${name}`)
  const destination = resolve(root, 'dist/licenses', name)
  await mkdir(destination, { recursive: true })
  for (const file of notices) await cp(resolve(source, file), resolve(destination, file), { recursive: true })
}
console.log('Third-party notices copied to dist/licenses')
