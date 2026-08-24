import { copyFile, mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(scriptDir, '..')
const projectDir = resolve(frontendDir, '..')
const source = resolve(frontendDir, 'node_modules', 'echarts', 'dist', 'echarts.min.js')
const targetDir = resolve(projectDir, 'vendor')
const target = resolve(targetDir, 'echarts.min.js')

await mkdir(targetDir, { recursive: true })
await copyFile(source, target)
console.log(`ECharts synced to ${target}`)

