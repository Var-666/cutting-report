import { test } from 'vitest'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript } from '@vue/compiler-sfc'
import { renderToString } from '@vue/server-renderer'
import { init, use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { scriptFunction } from './typescript-helpers.mjs'
import { weldingTrendOption, weldingProcessOption, weldingLengthOption } from '../src/utils/weldingChartOptions.ts'
import { weldingProcessRows, weldingMetricSeries, weldingDisplay } from '../src/utils/weldingReport.ts'

test('each welding report has one distinct chart and only distinct grouped data beside it', async () => {
  const bindings = {
    ...Vue, weldingProcessRows, weldingDisplay, weldingTrendOption, weldingLengthOption, weldingProcessOption,
    useReportChartTheme: () => ({ colors: Vue.ref({ primary: 'blue', secondary: 'green' }), vars: Vue.ref({}) }),
    ReportTrendPanel: { props: ['title', 'showTable'], setup: (props, { slots }) => () => Vue.h('section', [
      Vue.h('h2', props.title), slots.chart?.(), props.showTable ? slots.table?.() : null,
    ]) },
    AppReportTable: { setup: () => () => Vue.h('table') },
    WeldingChart: { props: ['label'], setup: props => () => Vue.h('div', { role: 'img', 'aria-label': props.label }) },
    NEmpty: { props: ['description'], setup: props => () => Vue.h('p', props.description) },
  }
  const descriptor = parse(readFileSync(new URL('../src/components/WeldingCharts.vue', import.meta.url), 'utf8')).descriptor
  const script = compileScript(descriptor, { id: 'welding-chart-layout', inlineTemplate: true }).content
    .replace(/import\s*\{([^}]+)\}\s*from\s*['"]vue['"]/g, (_, names) => {
      for (const name of names.split(',')) { const [original, alias = original] = name.trim().split(/\s+as\s+/); bindings[alias] = Vue[original] }
      return ''
    }).replace(/^import .*$/gm, '').replace('export default', 'return')
  const Charts = scriptFunction(...Object.keys(bindings), script)(...Object.values(bindings))
  const render = props => renderToString(Vue.createSSRApp(Charts, props))
  const report = {
    daily: [{ production_date: '2026-11-17', job_count: 2, weld_length_m: 10 }],
    device_distribution: [{ device_no: 1, weld_length_m: 10 }],
    equipment: [{ device_no: 1, robot_no: 1, job_count: 2, weld_length_m: 10 }],
    projects: [{ project_no: 'P1', part_count: 2, cumulative_actual_length_m: 10 }],
    summary: { flat_length_m: 10 },
  }
  for (const [kind, title, grouped] of [
    ['daily', '每日焊接趋势', false], ['parts', '项目工件分布', true],
    ['jobs', '焊接工艺结构', true], ['equipment', '设备产出对比', false],
  ]) {
    const html = await render({ kind, report })
    assert.ok(html.includes(title))
    assert.equal((html.match(/<h2\b/g) || []).length, 1)
    assert.equal(html.includes('<table'), grouped)
    for (const other of ['每日焊接趋势', '项目工件分布', '焊接工艺结构', '设备产出对比'].filter(value => value !== title)) assert.ok(!html.includes(other))
    assert.ok(!html.includes('焊接作业趋势') && !html.includes('设备生产贡献') && !html.includes('机器人工位分析'))
  }
  const empty = await render({ kind: 'jobs', report: { ...report, summary: {} } })
  assert.ok(empty.includes('暂无有效工艺长度数据'))
})

const flush = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)) }
function mountChart(t) {
  const props = Vue.reactive({ option: weldingTrendOption([{ production_date: '2026-10-26', weld_length_m: 12, job_count: 2 }], '#123456', '#abcdef'), label: 'trend' })
  const darkMode = Vue.ref(false), vars = Vue.ref({ textColor2: '#333', textColor1: '#111', popoverColor: '#fff', borderColor: '#ddd' })
  const charts = [], observers = []
  class ResizeObserver {
    constructor(callback) { this.callback = callback; this.nodes = []; observers.push(this) }
    observe(node) { this.nodes.push(node) }
    disconnect() { this.nodes = [] }
  }
  const bindings = {
    ...Vue, defineProps: () => props, use, BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, DataZoomComponent, SVGRenderer, ResizeObserver,
    useReportChartTheme: redraw => { Vue.watch([darkMode, vars], redraw, { flush: 'post' }); return { darkMode, vars } },
    init: (node, theme, options) => {
      const engine = init(null, theme, { ...options, ssr: true, width: 600, height: 280 })
      const chart = { theme, options, engine, disposals: 0, resizes: 0,
        setOption(option, settings) { engine.setOption(option, settings) },
        dispose() { chart.disposals++; engine.dispose() }, resize() { chart.resizes++ },
      }
      charts.push(chart)
      return chart
    },
  }
  const code = parse(readFileSync(new URL('../src/components/WeldingChart.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const setup = scriptFunction(...Object.keys(bindings), `${code}; return { element }`)
  const renderer = Vue.createRenderer({ createElement: () => ({}), createComment: () => ({}), createText: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { const state = setup(...Object.values(bindings)); return () => Vue.h('div', { ref: state.element }) } })
  app.mount({})
  return { props, charts, observers, darkMode, unmount: () => app.unmount() }
}

test('welding chart reuses its SVG instance, updates actual job counts and preserves legend selection', async t => {
  const { props, charts, observers, unmount } = mountChart()
  t.onTestFinished(unmount)
  await flush()
  charts[0].engine.dispatchAction({ type: 'legendUnSelect', name: '作业次数' })
  props.option = weldingTrendOption([{ production_date: '2026-10-27', weld_length_m: 22, job_count: 3 }], '#123456', '#abcdef')
  await flush()
  assert.equal(charts.length, 1)
  const option = charts[0].engine.getOption()
  assert.deepEqual(option.series.map(series => series.data), [[22], [3]])
  assert.equal(option.legend[0].selected['作业次数'], false)
  observers[0].callback()
  assert.equal(charts[0].resizes, 1)
})

test('theme change rebuilds the chart and unmount releases the instance and observer', async t => {
  const { charts, observers, darkMode, unmount } = mountChart()
  await flush()
  darkMode.value = true
  await flush()
  assert.equal(charts[0].disposals, 1)
  assert.equal(charts.length, 2)
  assert.equal(charts[1].theme, 'dark')
  assert.equal(observers.length, 1)
  unmount()
  assert.equal(charts[1].disposals, 1)
  assert.equal(observers[0].nodes.length, 0)
})

test('process and ranking options preserve unknown types, zero values and missing data without invented trends', () => {
  const rows = weldingProcessRows({ flat_length_m: 0, unknown_length_m: 2 })
  assert.equal(rows.find(row => row.key === 'flat_length_m').length, 0)
  assert.equal(rows.find(row => row.key === 'vertical_length_m').length, null)
  assert.deepEqual(weldingProcessOption(rows, []).series[0].data, [{ name: '未知工艺', value: 2 }])
  const ranking = weldingLengthOption(['设备0 / 0', '设备1 / 0'], [0, 5], '#123456')
  assert.deepEqual(ranking.yAxis.data, ['设备0 / 0', '设备1 / 0'])
  assert.deepEqual(ranking.series[0].data, [0, 5])
  assert.deepEqual(weldingMetricSeries({ daily: [{ production_date: '2026-10-27', job_count: 0 }, { production_date: '2026-10-26', job_count: 3 }] }, 'job_count'), [3, 0])
  assert.deepEqual(weldingMetricSeries({ daily: [{ production_date: '2026-10-27' }] }, 'working_hours'), [null])
})
