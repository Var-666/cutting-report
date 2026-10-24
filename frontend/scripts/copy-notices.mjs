import assert from 'node:assert/strict'
import { cp, mkdir, readdir, readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

const root = fileURLToPath(new URL('..', import.meta.url))
const { dependencies } = JSON.parse(await readFile(resolve(root, 'package.json'), 'utf8'))
for (const name of [...Object.keys(dependencies), 'zrender', 'tslib', 'soybean-admin']) {
  const source = resolve(root, name === 'soybean-admin' ? 'src/vendor/soybean-admin' : `node_modules/${name}`)
  const notices = (await readdir(source)).filter((file) => /^(LICENSE|COPYING|NOTICE)/i.test(file))
  assert.ok(notices.length, `Missing third-party notice: ${name}`)
  const destination = resolve(root, 'dist/licenses', name)
  await mkdir(destination, { recursive: true })
  for (const file of notices) await cp(resolve(source, file), resolve(destination, file), { recursive: true })
}
console.log('Third-party notices copied to dist/licenses')
