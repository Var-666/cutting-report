import type { EChartsOption } from 'echarts'
import type { WeldingDailyRow } from '../types/welding'
import type { weldingProcessRows } from './weldingReport'

export function weldingTrendOption(rows: WeldingDailyRow[], color: string, secondary: string): EChartsOption {
  return {
    color: [color, secondary], tooltip: { trigger: 'axis', confine: true },
    legend: { top: 8, itemWidth: 12, itemHeight: 8 },
    grid: { left: 52, right: 44, top: 60, bottom: 32, containLabel: true },
    xAxis: { type: 'category', data: rows.map(row => row.production_date.slice(5)), axisTick: { show: false } },
    yAxis: [{ type: 'value', name: 'm', splitNumber: 3 }, { type: 'value', name: '次', minInterval: 1, splitLine: { show: false } }],
    series: [
      { name: '焊接长度', type: 'bar', barMaxWidth: 26, itemStyle: { borderRadius: [3, 3, 0, 0] }, data: rows.map(row => row.weld_length_m) },
      { name: '作业次数', type: 'line', yAxisIndex: 1, symbolSize: 6, lineStyle: { width: 2 }, data: rows.map(row => row.job_count) },
    ],
  }
}

export function weldingLengthOption(labels: string[], values: (number | null)[], color: string, name = '焊接长度'): EChartsOption {
  return {
    tooltip: { trigger: 'axis', confine: true, valueFormatter: value => `${value} m` },
    grid: { left: 12, right: 60, top: 30, bottom: 24, containLabel: true },
    xAxis: { type: 'value', name: 'm', splitNumber: 3 },
    yAxis: { type: 'category', inverse: true, data: labels, axisLabel: { width: 100, overflow: 'truncate' }, axisTick: { show: false } },
    dataZoom: labels.length > 8 ? [{ type: 'inside', yAxisIndex: 0, startValue: 0, endValue: 7, filterMode: 'filter' }] : [],
    series: [{ name, type: 'bar', barMaxWidth: 22, data: values, label: { show: true, position: 'right', formatter: item => typeof item.value === 'number' ? item.value.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : '—' }, itemStyle: { color, borderRadius: [0, 3, 3, 0] } }],
  }
}

export function weldingProcessOption(rows: ReturnType<typeof weldingProcessRows>, colors: string[]): EChartsOption {
  return {
    color: colors, tooltip: { trigger: 'item', confine: true, valueFormatter: value => `${value} m` },
    legend: { bottom: 8, itemWidth: 10, itemHeight: 10 },
    series: [{ type: 'pie', radius: ['44%', '68%'], center: ['50%', '42%'], label: { show: false }, emphasis: { label: { show: true, formatter: '{b}\n{d}%' } },
      data: rows.filter(row => row.length !== null && row.length > 0).map(row => ({ name: row.name, value: row.length! })) }],
  }
}
