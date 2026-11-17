<script setup lang="ts">
import { computed } from 'vue'
import { NSkeleton } from 'naive-ui'
import HomeKpiCard from '../HomeKpiCard.vue'
import type { PdmReportSummaries } from '../../api/generated/types.gen'
import { pdmDocumentKinds } from '../../utils/pdmFilters'
import type { PdmDocumentKind } from '../../utils/pdmFilters'

const props = defineProps<{ overview: PdmReportSummaries | null; loading: boolean; activeKind?: PdmDocumentKind }>()
const emit = defineEmits<{ select: [kind: PdmDocumentKind] }>()
const cards = computed(() => pdmDocumentKinds.map(kind => {
  const counts = props.overview?.[kind.value]
  return { ...kind, counts, rates: [
    { label: '按时完成率', value: counts?.on_time_rate ?? null, tone: 'success' },
    { label: '总完成率', value: counts?.total_rate ?? null, tone: 'info' },
    { label: '未完成率', value: counts && counts.total > 0 ? (counts.total - counts.completed) / counts.total * 100 : null, tone: 'warning' },
  ] }
}))
</script>

<template>
  <div class="pdm-completion-cards">
    <HomeKpiCard v-for="card in cards" :key="card.value" :title="`${card.label}完成率`" :value="card.counts?.total_rate ?? null" unit="%" :precision="2" variant="report"
      :class="{ 'pdm-completion-cards__choice': activeKind, 'is-active': activeKind === card.value }" :role="activeKind ? 'button' : undefined" :tabindex="activeKind ? 0 : undefined" :aria-pressed="activeKind ? activeKind === card.value : undefined"
      @click="activeKind && emit('select', card.value)" @keydown.enter.prevent="activeKind && emit('select', card.value)" @keydown.space.prevent="activeKind && emit('select', card.value)"
      :icon="card.value === 'equipment' ? 'briefcase' : 'file'" :color="card.value === 'equipment' ? 'rgb(var(--info-color))' : 'rgb(var(--success-color))'" :loading="loading"
      :trend-label="card.counts ? `全部数据 · 总数 ${card.counts.total} · 已完成 ${card.counts.completed} · 未完成 ${card.counts.total - card.counts.completed}` : loading ? '正在加载全部数据' : '全部数据统计暂不可用'">
      <template #value>
        <dl class="pdm-completion-cards__rates">
          <div v-for="rate in card.rates" :key="rate.label" class="pdm-completion-cards__rate" :class="`pdm-completion-cards__rate--${rate.tone}`">
            <dt>{{ rate.label }}</dt>
            <dd><NSkeleton v-if="loading" height="30px" width="80%" :aria-label="`${card.label}${rate.label}正在加载`" role="status" /><span v-else :class="{ 'pdm-completion-cards__missing': rate.value === null }">{{ rate.value === null ? '—' : rate.value.toFixed(2) }}<small v-if="rate.value !== null">%</small></span></dd>
          </div>
        </dl>
      </template>
    </HomeKpiCard>
  </div>
</template>

<style scoped>
.pdm-completion-cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--space-4)}
.pdm-completion-cards :deep(.home-kpi--report){min-height:180px;padding:var(--space-6)}
.pdm-completion-cards__choice{cursor:pointer}
.pdm-completion-cards__choice.is-active{outline:2px solid rgb(var(--primary-color));outline-offset:2px}
.pdm-completion-cards__choice:focus-visible{outline:2px solid rgb(var(--primary-color));outline-offset:4px}
.pdm-completion-cards__rates{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:var(--space-4);margin:0}
.pdm-completion-cards__rate dt{margin-bottom:var(--space-2);color:var(--text-color-2);font-size:var(--font-auxiliary)}
.pdm-completion-cards__rate dd{margin:0;color:rgb(var(--info-color));font-size:var(--font-kpi-value);font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap}
.pdm-completion-cards__rate--success dd{color:rgb(var(--success-color))}
.pdm-completion-cards__rate--warning dd{color:rgb(var(--warning-color))}
.pdm-completion-cards__rate small{margin-left:4px;font-size:var(--font-auxiliary);font-weight:400}
.pdm-completion-cards__missing{color:var(--text-color-3)}
</style>
