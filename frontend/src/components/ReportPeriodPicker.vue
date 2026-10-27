<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, useId, watch } from 'vue'
import { createActivationBinding } from '../utils/activationTimer.ts'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  startDate: { type: String, required: true },
  endDate: { type: String, required: true },
  anchorDate: { type: String, default: '' },
  disabled: Boolean,
  maxDays: { type: Number, default: 366 },
})
const emit = defineEmits<{ 'update:startDate': [date: string]; 'update:endDate': [date: string]; apply: [period: { startDate: string; endDate: string; preset: string }]; 'validation-error': [message: string] }>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const open = ref(false)
const panelStyle = ref({})
const draftStart = ref(props.startDate)
const draftEnd = ref(props.endDate)
const validationError = ref('')
const panelId = useId()
const labelId = useId()
const valueId = useId()

function parseDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || '')
  if (!match) return null
  const result = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
  return Number.isNaN(result.getTime()) ? null : result
}

function formatDate(value: Date) {
  const year = value.getUTCFullYear()
  const month = String(value.getUTCMonth() + 1).padStart(2, '0')
  const day = String(value.getUTCDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function addDays(value: Date, days: number) {
  const result = new Date(value)
  result.setUTCDate(result.getUTCDate() + days)
  return result
}

function monthStart(value: Date) {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), 1))
}

function quarterStart(value: Date) {
  return new Date(Date.UTC(
    value.getUTCFullYear(),
    Math.floor(value.getUTCMonth() / 3) * 3,
    1,
  ))
}

const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Shanghai' })
const anchor = computed(() => parseDate(props.anchorDate || props.endDate || today) || parseDate(today)!)
const presets = computed(() => {
  const end = anchor.value
  const currentMonthStart = monthStart(end)
  const previousMonthEnd = addDays(currentMonthStart, -1)
  const currentQuarterStart = quarterStart(end)
  const previousQuarterEnd = addDays(currentQuarterStart, -1)
  return [
    { id: 'single', label: '单日', start: end, end },
    { id: 'last7', label: '最近 7 天', start: addDays(end, -6), end },
    { id: 'last30', label: '跨月·近 30 天', start: addDays(end, -29), end },
    { id: 'month', label: '本月', start: currentMonthStart, end },
    {
      id: 'previous_month',
      label: '上月',
      start: monthStart(previousMonthEnd),
      end: previousMonthEnd,
    },
    { id: 'quarter', label: '本季度', start: currentQuarterStart, end },
    {
      id: 'previous_quarter',
      label: '上季度',
      start: quarterStart(previousQuarterEnd),
      end: previousQuarterEnd,
    },
  ].map((item) => ({
    ...item,
    start: formatDate(item.start),
    end: formatDate(item.end),
  }))
})

function matchingPreset(start: string, end: string) {
  return presets.value.find((item) => item.start === start && item.end === end) || null
}

const currentPreset = computed(() => matchingPreset(props.startDate, props.endDate))
const draftPreset = computed(() => matchingPreset(draftStart.value, draftEnd.value))
const buttonLabel = computed(() => {
  const prefix = currentPreset.value?.label || '自定义'
  const period = props.startDate === props.endDate
    ? props.startDate
    : `${props.startDate} 至 ${props.endDate}`
  return `${prefix} · ${period}`
})

function positionPanel() {
  if (!open.value || !trigger.value || !panel.value) return
  const rect = trigger.value.getBoundingClientRect()
  const viewportPadding = 10
  const gutter = 7
  const width = Math.min(470, window.innerWidth - viewportPadding * 2)
  const left = Math.min(
    Math.max(viewportPadding, rect.right - width),
    window.innerWidth - width - viewportPadding,
  )
  const availableBelow = window.innerHeight - rect.bottom - viewportPadding - gutter
  const availableAbove = rect.top - viewportPadding - gutter
  const placeAbove = availableBelow < 330 && availableAbove > availableBelow
  const maxHeight = Math.max(230, placeAbove ? availableAbove : availableBelow)
  panelStyle.value = {
    left: `${Math.round(left)}px`,
    top: placeAbove ? 'auto' : `${Math.round(rect.bottom + gutter)}px`,
    bottom: placeAbove ? `${Math.round(window.innerHeight - rect.top + gutter)}px` : 'auto',
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.floor(maxHeight)}px`,
  }
}

async function showPanel() {
  if (props.disabled) return
  draftStart.value = props.startDate
  draftEnd.value = props.endDate
  validationError.value = ''
  open.value = true
  await nextTick()
  positionPanel()
}

function closePanel({ focus = false } = {}) {
  if (!open.value) return
  open.value = false
  validationError.value = ''
  if (focus) nextTick(() => trigger.value?.focus())
}

function selectPreset(item: { start: string; end: string }) {
  draftStart.value = item.start
  draftEnd.value = item.end
  validationError.value = ''
}

function validate() {
  const start = parseDate(draftStart.value)
  const end = parseDate(draftEnd.value)
  if (!start || !end) return '请选择完整的开始和结束日期'
  if (end < start) return '结束日期不能早于开始日期'
  const inclusiveDays = Math.round((end.getTime() - start.getTime()) / 86400000) + 1
  if (inclusiveDays > props.maxDays) return `查询区间最长为 ${props.maxDays} 天`
  return ''
}

function applyPeriod() {
  validationError.value = validate()
  if (validationError.value) {
    emit('validation-error', validationError.value)
    return
  }
  emit('update:startDate', draftStart.value)
  emit('update:endDate', draftEnd.value)
  emit('validation-error', '')
  emit('apply', {
    startDate: draftStart.value,
    endDate: draftEnd.value,
    preset: draftPreset.value?.id || 'custom',
  })
  closePanel({ focus: true })
}

function handleDocumentPointer(event: PointerEvent) {
  const target = event.target as HTMLElement
  if (
    root.value?.contains(target)
    || panel.value?.contains(target)
    || target.closest?.('.app-date-picker__panel')
  ) return
  closePanel()
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closePanel({ focus: true })
}

function handleViewportChange() {
  if (open.value) positionPanel()
}

watch(() => props.disabled, (disabled) => {
  if (disabled) closePanel()
})

const listenerBinding = createActivationBinding(
  () => {
    document.addEventListener('pointerdown', handleDocumentPointer)
    document.addEventListener('keydown', handleKeydown)
    window.addEventListener('resize', handleViewportChange)
    window.addEventListener('scroll', handleViewportChange, true)
  },
  () => {
    document.removeEventListener('pointerdown', handleDocumentPointer)
    document.removeEventListener('keydown', handleKeydown)
    window.removeEventListener('resize', handleViewportChange)
    window.removeEventListener('scroll', handleViewportChange, true)
  },
)

function pause() {
  closePanel()
  listenerBinding.deactivate()
}

onMounted(listenerBinding.activate)
onActivated(listenerBinding.activate)
onDeactivated(pause)
onBeforeUnmount(pause)
</script>

<template>
  <div ref="root" class="report-period-picker" :class="{ 'is-open': open }">
    <span :id="labelId" class="report-period-picker__label"><AppIcon name="calendar" size="13" tone="current" />统计区间</span>
    <button
      ref="trigger"
      type="button"
      class="report-period-picker__trigger"
      :disabled="disabled"
      aria-haspopup="dialog"
      :aria-labelledby="`${labelId} ${valueId}`"
      :aria-expanded="open"
      :aria-controls="open ? panelId : undefined"
      @click="open ? closePanel() : showPanel()"
    >
      <span :id="valueId" class="report-period-picker__value">{{ buttonLabel }}</span><i aria-hidden="true"></i>
    </button>

    <Teleport to="body">
      <section
        v-if="open"
        :id="panelId"
        ref="panel"
        class="report-period-picker__panel"
        :style="panelStyle"
        role="dialog"
        aria-modal="false"
        aria-label="选择报表统计区间"
      >
        <header>
          <div><small>REPORT PERIOD</small><strong>选择统计区间</strong></div>
          <button type="button" aria-label="关闭区间选择" @click="closePanel({ focus: true })">×</button>
        </header>

        <div class="report-period-picker__presets" aria-label="快捷区间">
          <button
            v-for="item in presets"
            :key="item.id"
            type="button"
            :class="{ selected: draftPreset?.id === item.id }"
            @click="selectPreset(item)"
          >{{ item.label }}</button>
        </div>

        <div class="report-period-picker__dates">
          <label><span>开始日期</span><AppDatePicker v-model="draftStart" required /></label>
          <label><span>结束日期</span><AppDatePicker v-model="draftEnd" required /></label>
        </div>
        <p v-if="validationError" class="report-period-picker__error" role="alert">{{ validationError }}</p>
        <p v-else class="report-period-picker__hint">包含开始日和结束日，最长 {{ maxDays }} 天。</p>

        <footer>
          <button type="button" @click="closePanel({ focus: true })">取消</button>
          <button type="button" class="primary" @click="applyPeriod">应用</button>
        </footer>
      </section>
    </Teleport>
  </div>
</template>

<style scoped>
.report-period-picker { position:relative; min-width:0; width:100%; grid-column:span 2; }
.report-period-picker__label { display:flex; align-items:center; gap:5px; margin-bottom:6px; color:var(--text-color-3); font-size:var(--font-auxiliary); font-weight:700; letter-spacing:.12px; }
.report-period-picker__trigger { display:flex; align-items:center; justify-content:space-between; gap:12px; width:100%; height:40px; min-height:40px; padding:0 12px; color:var(--ink); border:1px solid var(--theme-border-color); border-radius:8px; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); text-align:left; transition:border-color .16s ease,box-shadow .16s ease,background .16s ease; }
.report-period-picker__trigger:hover:not(:disabled) { border-color:var(--theme-border-color); background:rgb(var(--container-bg-color)); }
.report-period-picker__value { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.report-period-picker__trigger i { width:8px; height:8px; flex:0 0 8px; border-right:2px solid var(--theme-border-color); border-bottom:2px solid var(--theme-border-color); transform:rotate(45deg) translateY(-2px); transition:transform .16s ease; }
.report-period-picker.is-open .report-period-picker__trigger { border-color:var(--blue); box-shadow:var(--theme-shadow); }
.report-period-picker.is-open .report-period-picker__trigger i { transform:rotate(225deg) translate(-2px,-1px); }
.report-period-picker__panel { position:fixed; z-index:1850; overflow:auto; padding:0; border:1px solid var(--theme-border-color); border-radius:6px; background:rgb(var(--container-bg-color)); box-shadow:var(--theme-shadow); overscroll-behavior:contain; }
.report-period-picker__panel > header { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:15px 17px; border-bottom:1px solid var(--theme-border-color); }
.report-period-picker__panel > header div { display:grid; gap:2px; }
.report-period-picker__panel > header small { color:rgb(var(--info-color)); font-size:var(--font-auxiliary); font-weight:800; letter-spacing:.11em; }
.report-period-picker__panel > header strong { color:var(--navy); font-size:16px; }
.report-period-picker__panel > header button { min-width:34px; min-height:34px; padding:0; color:var(--text-color-3); border:0; background:transparent; font-size:24px; }
.report-period-picker__presets { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:7px; padding:15px 17px 8px; }
.report-period-picker__presets button { min-width:0; min-height:37px; padding:6px 8px; color:rgb(var(--info-color)); border:1px solid var(--theme-border-color); border-radius:4px; background:rgb(var(--container-bg-color)); font-size:12px; }
.report-period-picker__presets button:hover { color:var(--blue); border-color:var(--theme-border-color); background:rgb(var(--info-color) / .1); }
.report-period-picker__presets button.selected { color:var(--theme-on-primary-color); border-color:var(--blue); background:var(--blue); font-weight:700; }
.report-period-picker__dates { display:grid; grid-template-columns:1fr 1fr; gap:11px; padding:10px 17px 8px; }
.report-period-picker__dates label { min-width:0; }
.report-period-picker__dates label > span { display:block; margin-bottom:6px; color:var(--text-color-3); font-size:var(--font-auxiliary); font-weight:700; }
.report-period-picker__hint,.report-period-picker__error { min-height:20px; margin:3px 17px 8px; font-size:var(--font-auxiliary); line-height:1.55; }
.report-period-picker__hint { color:var(--text-color-3); }
.report-period-picker__error { color:rgb(var(--error-color)); }
.report-period-picker__panel > footer { display:flex; justify-content:flex-end; gap:9px; padding:12px 17px 15px; border-top:1px solid var(--theme-border-color); }
.report-period-picker__panel > footer button { min-width:82px; min-height:38px; }
@media (max-width:560px) {
  .report-period-picker { grid-column:auto; }
  .report-period-picker__panel { inset:auto 10px 10px!important; width:auto!important; max-height:calc(100vh - 20px)!important; border-radius:9px; }
  .report-period-picker__presets { grid-template-columns:repeat(2,minmax(0,1fr)); }
  .report-period-picker__dates { grid-template-columns:1fr; }
}
</style>
