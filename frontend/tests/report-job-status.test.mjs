import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const source = readFileSync(new URL('../src/components/ReportJobStatus.vue', import.meta.url), 'utf8')

test('report job status gives unavailable AI output a user-facing label', () => {
  assert.match(source, /unavailable: '暂不可用'/)
  assert.match(source, /aiStatusText\[job\.ai_status \|\| ''\]/)
})
