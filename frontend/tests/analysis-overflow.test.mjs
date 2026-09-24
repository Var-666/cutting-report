import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const source = readFileSync(new URL('../src/components/AnalysisPanel.vue', import.meta.url), 'utf8')

test('long analysis copy remains available through an accessible per-card expander', () => {
  assert.doesNotMatch(source, /sentence\.length\s*>\s*34|sentence\.slice\(0,\s*34\)/)
  assert.match(source, /const expandedSections = ref\(new Set\(\)\)/)
  assert.match(source, /class="analysis-expand"/)
  assert.match(source, /:aria-expanded="isSectionExpanded\(section\.key\)"/)
  assert.match(source, /:aria-controls="`analysis-copy-\$\{section\.key\}`"/)
  assert.match(source, /展开全文/)
  assert.match(source, /收起/)
  assert.match(source, /\.analysis-grid h4\{[^}]*-webkit-line-clamp:2/)
  assert.match(source, /\.analysis-grid article\.is-expanded h4,\.analysis-grid article\.is-expanded p\{[^}]*overflow:visible/)
})

test('single-sentence analysis is not repeated as both heading and body', () => {
  assert.match(source, /<p v-if="sectionBody\(analysis\[section\.key\]\)"/)
  assert.doesNotMatch(source, /sectionBody\(analysis\[section\.key\]\) \|\| plainAnalysisText/)
})

test('core metric cards omit technical source labels while evidence keeps traceability', () => {
  assert.doesNotMatch(source, /<small>\{\{ fact\.source \|\| '当前报表数据' \}\}<\/small>/)
  assert.match(source, /<small>来源：\{\{ fact\.source \|\| '当前报表数据' \}\}<\/small>/)
  assert.match(source, /\.analysis-facts article\{[^}]*min-height:104px[^}]*align-items:center/)
  assert.match(source, /\.analysis-facts strong\{[^}]*font-size:18px!important/)
})
