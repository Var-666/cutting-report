<script setup>
import { computed } from 'vue'
import { sparklineGeometry } from '../utils/sparkline.js'

const props = defineProps({
  values: { type: Array, default: () => [] },
  label: { type: String, default: '指标趋势' },
  maxPoints: { type: Number, default: 12 },
})

const geometry = computed(() => sparklineGeometry(props.values, { maxPoints: props.maxPoints }))
const accessibleLabel = computed(() => geometry.value.count
  ? `${props.label}，基于最近 ${geometry.value.count} 个有效数据点`
  : `${props.label}，暂无趋势数据`)
</script>

<template>
  <svg
    class="data-sparkline"
    :class="[`is-${geometry.direction}`, { 'is-empty': !geometry.count }]"
    viewBox="0 0 76 28"
    role="img"
    :aria-label="accessibleLabel"
  >
    <line class="data-sparkline__baseline" x1="3" y1="25" x2="73" y2="25" />
    <path v-if="geometry.path" class="data-sparkline__line" :d="geometry.path" />
    <circle
      v-if="geometry.lastPoint"
      class="data-sparkline__point"
      :cx="geometry.lastPoint.x"
      :cy="geometry.lastPoint.y"
      r="2.2"
    />
    <text v-if="!geometry.count" x="38" y="17" text-anchor="middle">暂无趋势</text>
  </svg>
</template>

<style scoped>
.data-sparkline{display:block;overflow:visible;color:var(--kpi-color,rgb(var(--primary-color)))}
.data-sparkline__baseline{stroke:currentColor;stroke-width:1;opacity:.14}
.data-sparkline__line{fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round;vector-effect:non-scaling-stroke}
.data-sparkline__point{fill:rgb(var(--container-bg-color));stroke:currentColor;stroke-width:1.6;vector-effect:non-scaling-stroke}
.data-sparkline text{fill:var(--text-color-3);font-size:11px}
.data-sparkline.is-empty{opacity:.76}
</style>
