// Regenerate the three migrated API modules without running Web/Worker or accessing databases.
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createClient } from '@hey-api/openapi-ts'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const check = process.argv.includes('--check')
const temporary = mkdtempSync(join(tmpdir(), 'cutting-contracts-'))
const venv = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
const python = process.env.PYTHON || (existsSync(venv) ? venv : process.platform === 'win32' ? 'python' : 'python3')
try {
  const schema = join(temporary, 'openapi.json')
  const exported = spawnSync(python, [join(root, 'scripts/export_api_contracts.py'), schema], { stdio: 'inherit' })
  if (exported.error) throw exported.error
  if (exported.status !== 0) throw new Error('FastAPI contract export failed')
  const output = check ? join(temporary, 'generated') : join(root, 'frontend/src/api/generated')
  await createClient({ input: schema, output, plugins: ['@hey-api/typescript'] })
  if (check) {
    const tracked = join(root, 'frontend/src/api/generated')
    for (const name of readdirSync(output)) {
      if (!existsSync(join(tracked, name)) || readFileSync(join(tracked, name), 'utf8') !== readFileSync(join(output, name), 'utf8')) {
        throw new Error(`API types are stale: ${name}. Run pnpm run api:generate.`)
      }
    }
  }
} finally {
  rmSync(temporary, { recursive: true, force: true })
}
