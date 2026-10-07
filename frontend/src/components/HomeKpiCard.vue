<script setup>
import AppIcon from './AppIcon.vue'
import MetricSparkline from './MetricSparkline.vue'

defineProps({
  title: { type: String, required: true },
  icon: { type: String, required: true },
  tone: { type: String, default: 'blue' },
  value: { type: [String, Number], default: '--' },
  unit: { type: String, default: '' },
  status: { type: String, default: '' },
  direction: { type: String, default: 'flat' },
  series: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})
</script>

<template>
  <article class="home-kpi" :class="`home-kpi--${tone}`" :aria-label="`${title}：${loading ? '正在加载' : `${value} ${unit}`}`">
    <div class="home-kpi__heading">
      <span class="home-kpi__icon"><AppIcon :name="icon" :size="20" tone="muted" /></span>
      <div><strong>{{ title }}</strong></div>
    </div>
    <div v-if="loading" class="home-kpi__skeleton" role="status" aria-label="正在获取生产数据"><i></i><i></i></div>
    <template v-else>
      <div class="home-kpi__reading"><strong>{{ value }}</strong><span v-if="unit">{{ unit }}</span></div>
      <div class="home-kpi__foot">
        <span class="home-kpi__status" :class="`is-${direction}`">{{ status }}</span>
        <MetricSparkline class="home-kpi__sparkline" :values="series" :label="`${title}趋势`" />
      </div>
    </template>
  </article>
</template>

<style scoped>
.home-kpi{--kpi-color:#56768c;--kpi-soft:#edf3f6;min-width:0;min-height:146px;display:flex;flex-direction:column;justify-content:space-between;padding:16px;border:1px solid var(--home-border,#dfe7ee);border-radius:12px;background:#fff;box-shadow:0 2px 8px rgba(15,45,70,.04);transition:border-color .18s ease,box-shadow .18s ease}
.home-kpi:hover{border-color:#c7d8e5;box-shadow:0 5px 14px rgba(15,45,70,.07)}
.home-kpi--teal{--kpi-color:#548482;--kpi-soft:#eef5f4}.home-kpi--purple{--kpi-color:#7d7897;--kpi-soft:#f3f2f7}.home-kpi--green{--kpi-color:#548482;--kpi-soft:#eef5f4}.home-kpi--amber{--kpi-color:#617c90;--kpi-soft:#edf3f6}.home-kpi--sky{--kpi-color:#587f96;--kpi-soft:#edf3f6}
.home-kpi__heading{display:flex;align-items:center;gap:11px;min-width:0}.home-kpi__icon{width:38px;height:38px;display:grid;place-items:center;flex:0 0 auto;color:var(--kpi-color);border-radius:10px;background:var(--kpi-soft)}.home-kpi__heading>div{min-width:0}.home-kpi__heading strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.home-kpi__heading strong{color:#16324a;font-size:14px;font-weight:700}
.home-kpi__reading{display:flex;align-items:baseline;justify-content:center;flex-wrap:wrap;gap:2px 5px;min-width:0;color:#16324a}.home-kpi__reading strong{min-width:0;overflow-wrap:anywhere;font-size:clamp(20px,1.6vw,28px);font-weight:750;font-variant-numeric:tabular-nums;letter-spacing:-.035em;line-height:1.15}.home-kpi__reading span{flex:0 0 auto;color:#415e75;font-size:12px}
.home-kpi__foot{display:flex;align-items:end;justify-content:space-between;gap:8px;min-height:27px;padding-left:49px}.home-kpi__status{color:#415e75;font-size:12px;line-height:1.35}.home-kpi__status.is-up{color:#126a46}.home-kpi__status.is-down{color:#b33036}.home-kpi__sparkline{width:70px;height:27px;flex:0 0 auto}
.home-kpi__skeleton{display:grid;gap:9px;padding-left:49px}.home-kpi__skeleton i{display:block;width:76%;height:20px;border-radius:5px;background:#edf2f6;animation:kpi-pulse 1.3s ease-in-out infinite alternate}.home-kpi__skeleton i+ i{width:52%;height:10px}
@keyframes kpi-pulse{to{opacity:.48}}
.home-kpi__sparkline :deep(text){fill:#415e75;font-size:13px}
.home-kpi__sparkline.is-empty{opacity:1}
@media(max-width:1500px){.home-kpi{padding:13px}.home-kpi__foot,.home-kpi__skeleton{padding-left:0}}
@media(prefers-reduced-motion:reduce){.home-kpi,.home-kpi__skeleton i{animation:none;transition:none}}
</style>
