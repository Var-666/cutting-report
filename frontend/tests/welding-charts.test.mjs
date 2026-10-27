import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse } from '@vue/compiler-sfc'
import { init, use } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import { scriptFunction } from './typescript-helpers.mjs'

const flush = async () => { await Vue.nextTick(); await new Promise(resolve => setImmediate(resolve)) }
const report = () => ({ daily: [{ production_date: '2026-10-26', weld_length_m: 12, involved_part_count: 2 }], summary: { flat_length_m: 12 }, projects: [], equipment: [], device_distribution: [], rows: [], query: { page: 1 } })
function mountCharts(t, { visible = true, kind = 'daily' } = {}) {
  const props = Vue.reactive({ report: report(), kind })
  const show = Vue.ref(visible), darkMode = Vue.ref(false), colors = Vue.ref({ primary: '#123456' })
  const charts = [], observers = []
  class ResizeObserver {
    constructor(callback) { this.callback = callback; this.nodes = []; this.disconnects = 0; observers.push(this) }
    observe(node) { this.nodes.push(node) }
    disconnect() { this.nodes = []; this.disconnects++ }
  }
  const bindings = {
    ...Vue, defineProps: () => props, use, BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer, ResizeObserver,
    useReportChartTheme: redraw => { Vue.watch([darkMode, colors], redraw, { flush: 'post' }); return { darkMode, colors } },
    init: (node, theme, options) => {
      const engine = init(null, theme, { ...options, ssr: true, width: 600, height: 290 })
      const chart = { node, theme, options, engine, updates: [], disposals: 0, resizes: 0, getDom: () => node,
        setOption(option, settings) { chart.updates.push(settings); engine.setOption(option, settings) },
        dispose() { chart.disposals++; engine.dispose() }, resize() { chart.resizes++ },
      }
      charts.push(chart)
      return chart
    },
  }
  const code = parse(readFileSync(new URL('../src/components/WeldingCharts.vue', import.meta.url), 'utf8')).descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
  const setup = scriptFunction(...Object.keys(bindings), `${code}; return { first, second }`)
  let state
  const renderer = Vue.createRenderer({ createElement: () => ({}), createComment: () => ({}), createText: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} })
  const app = renderer.createApp({ setup() { state = setup(...Object.values(bindings)); return () => Vue.h('section', null, show.value ? [Vue.h('div', { ref: state.first }), Vue.h('div', { ref: state.second })] : []) } })
  app.mount({})
  let mounted = true
  const unmount = () => { if (mounted) { mounted = false; app.unmount() } }
  t.after(unmount)
  return { props, charts, observers, show, darkMode, colors, unmount }
}

test('welding charts initialize two SVG instances once and reuse them for plotted data updates', async t => {
  const { props, charts, observers } = mountCharts(t)
  await flush()
  assert.equal(charts.length, 2)
  assert.ok(charts.every(chart => chart.options.renderer === 'svg' && chart.updates.length === 1))
  assert.equal(observers.length, 1)
  assert.equal(observers[0].nodes.length, 2)
  charts[0].engine.dispatchAction({ type: 'legendUnSelect', name: '参与工件数' })
  props.report = { ...report(), daily: [{ production_date: '2026-10-27', weld_length_m: 22, involved_part_count: 3 }] }
  await flush()
  assert.equal(charts.length, 2)
  assert.ok(charts.every(chart => chart.disposals === 0 && chart.updates.length === 2))
  const option = charts[0].engine.getOption()
  assert.deepEqual(option.series[0].data, [22])
  assert.equal(option.legend[0].selected['参与工件数'], false, 'normal updates retain legend selection')
  assert.equal(charts[0].updates.at(-1).notMerge, false)
  assert.equal(observers[0].disconnects, 0)
  observers[0].callback()
  assert.ok(charts.every(chart => chart.resizes === 1))
})

test('welding charts ignore pagination, unplotted fields and snapshot metadata', async t => {
  const { props, charts } = mountCharts(t)
  await flush()
  props.report = { ...structuredClone(report()), query: { page: 2 }, rows: [{ id: 2 }], data_version: 'next', snapshot: { stale: true }, summary: { flat_length_m: 12, job_count: 99 } }
  await flush()
  assert.equal(charts.length, 2)
  assert.ok(charts.every(chart => chart.updates.length === 1 && chart.disposals === 0))
  props.report.summary.flat_length_m = 20
  await flush()
  assert.equal(charts[1].engine.getOption().series[0].data[0].value, 20)
  assert.equal(charts[1].updates.length, 2)
})

test('welding chart kind changes clear old series, axes and legends; empty data clears plotted values', async t => {
  const { props, charts } = mountCharts(t)
  await flush()
  props.kind = 'equipment'
  props.report = { ...report(), equipment: [{ device_no: 0, robot_no: 0, weld_length_m: 14, working_hours: 2 }], device_distribution: [{ device_no: 0, weld_length_m: 14 }] }
  await flush()
  assert.equal(charts.length, 2)
  assert.equal(charts[1].engine.getOption().series.length, 2)
  assert.deepEqual(charts[1].engine.getOption().xAxis[0].data, ['0 / 0'])
  props.kind = 'parts'
  props.report = { ...report(), projects: [{ project_no: 'P', cumulative_actual_length_m: 18, part_count: 4 }] }
  await flush()
  let option = charts[1].engine.getOption()
  assert.equal(option.series.length, 1)
  assert.equal(option.yAxis.length, 1)
  assert.equal(option.legend?.length || 0, 0)
  assert.deepEqual(option.series[0].data, [4])
  props.kind = 'jobs'
  await flush()
  option = charts[1].engine.getOption()
  assert.equal(option.series[0].type, 'pie')
  assert.equal(option.xAxis?.length || 0, 0)
  assert.equal(charts[1].updates.at(-1).notMerge, true)
  props.report = { ...report(), daily: [], summary: {} }
  await flush()
  assert.deepEqual(charts[0].engine.getOption().series[0].data, [])
  assert.deepEqual(charts[1].engine.getOption().series[0].data, [])
  assert.equal(charts.length, 2)
})

test('welding theme changes rebuild once, use current colors and leave the observer registered once', async t => {
  const { charts, observers, darkMode, colors } = mountCharts(t)
  await flush()
  darkMode.value = true
  colors.value = { primary: '#abcdef' }
  await flush()
  assert.equal(charts.length, 4)
  assert.ok(charts.slice(0, 2).every(chart => chart.disposals === 1))
  assert.ok(charts.slice(2).every(chart => chart.theme === 'dark' && chart.updates.length === 1))
  assert.equal(charts[2].engine.getOption().series[0].itemStyle.color, '#abcdef')
  assert.equal(observers.length, 1)
  assert.equal(observers[0].nodes.length, 2)
  assert.equal(observers[0].disconnects, 0)
  colors.value = { primary: '#654321' }
  await flush()
  assert.equal(charts.length, 6)
  assert.equal(charts[4].engine.getOption().series[0].itemStyle.color, '#654321')
})

test('welding missing or replaced DOM and unmount release instances and observer without late initialization', async t => {
  const { charts, observers, show, props, unmount } = mountCharts(t, { visible: false })
  await flush()
  assert.equal(charts.length, 0)
  assert.equal(observers[0].nodes.length, 0)
  show.value = true; await flush()
  assert.equal(charts.length, 2)
  show.value = false; await flush()
  assert.ok(charts.every(chart => chart.disposals === 1))
  assert.equal(observers[0].nodes.length, 0)
  show.value = true; await flush()
  assert.equal(charts.length, 4)
  props.report.daily = []
  unmount(); await flush()
  assert.ok(charts.every(chart => chart.disposals === 1))
  assert.equal(observers[0].nodes.length, 0)
  assert.equal(charts.length, 4)
})
