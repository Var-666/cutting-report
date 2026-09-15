<script setup>
import {
  computed,
  nextTick,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  ref,
  useAttrs,
  useId,
  watch,
} from 'vue'
import { createActivationBinding } from '../utils/activationTimer.js'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: String, default: '' },
  mode: { type: String, default: 'date', validator: (value) => ['date', 'month'].includes(value) },
  disabled: Boolean,
  required: Boolean,
  id: { type: String, default: undefined },
  min: { type: String, default: '' },
  max: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'change'])
const attrs = useAttrs()
const panelId = useId()
const root = ref(null)
const trigger = ref(null)
const panel = ref(null)
const open = ref(false)
const invalid = ref(false)
const panelStyle = ref({})
const now = new Date()
const viewYear = ref(now.getFullYear())
const viewMonth = ref(now.getMonth())
let containingForm = null

const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const months = Array.from({ length: 12 }, (_, index) => `${index + 1}月`)

function pad(value) {
  return String(value).padStart(2, '0')
}

function dateValue(year, month, day) {
  return `${year}-${pad(month + 1)}-${pad(day)}`
}

function monthValue(year, month) {
  return `${year}-${pad(month + 1)}`
}

function parsedValue() {
  const pattern = props.mode === 'month' ? /^(\d{4})-(\d{2})$/ : /^(\d{4})-(\d{2})-(\d{2})$/
  const match = pattern.exec(props.modelValue || '')
  if (!match) return null
  return { year: Number(match[1]), month: Number(match[2]) - 1, day: Number(match[3] || 1) }
}

const todayValue = computed(() => dateValue(now.getFullYear(), now.getMonth(), now.getDate()))
const thisMonthValue = computed(() => monthValue(now.getFullYear(), now.getMonth()))
const displayValue = computed(() => {
  const value = parsedValue()
  if (!value) return props.mode === 'month' ? '请选择月份' : '请选择日期'
  return props.mode === 'month'
    ? `${value.year}年${pad(value.month + 1)}月`
    : `${value.year}年${pad(value.month + 1)}月${pad(value.day)}日`
})
const panelTitle = computed(() => props.mode === 'month'
  ? `${viewYear.value}年`
  : `${viewYear.value}年${viewMonth.value + 1}月`)

function isOutsideBounds(value) {
  const comparable = props.mode === 'month' ? value.slice(0, 7) : value
  const minimum = props.mode === 'month' ? props.min.slice(0, 7) : props.min
  const maximum = props.mode === 'month' ? props.max.slice(0, 7) : props.max
  return Boolean((minimum && comparable < minimum) || (maximum && comparable > maximum))
}

const calendarDays = computed(() => {
  const firstWeekday = (new Date(viewYear.value, viewMonth.value, 1).getDay() + 6) % 7
  const firstVisible = new Date(viewYear.value, viewMonth.value, 1 - firstWeekday)
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(firstVisible.getFullYear(), firstVisible.getMonth(), firstVisible.getDate() + index)
    const value = dateValue(date.getFullYear(), date.getMonth(), date.getDate())
    return {
      value,
      day: date.getDate(),
      currentMonth: date.getMonth() === viewMonth.value,
      today: value === todayValue.value,
      disabled: isOutsideBounds(value),
    }
  })
})

function syncView() {
  const selected = parsedValue()
  const base = selected || { year: now.getFullYear(), month: now.getMonth() }
  viewYear.value = base.year
  viewMonth.value = base.month
}

function positionPanel() {
  if (!open.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const gutter = 6
  const viewportPadding = 10
  const desiredWidth = props.mode === 'month' ? 312 : 336
  const width = Math.min(Math.max(rect.width, desiredWidth), window.innerWidth - viewportPadding * 2)
  const left = Math.min(Math.max(viewportPadding, rect.left), window.innerWidth - width - viewportPadding)
  const availableBelow = Math.max(88, window.innerHeight - rect.bottom - viewportPadding - gutter)
  panelStyle.value = {
    top: `${Math.round(rect.bottom + gutter)}px`,
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.floor(availableBelow)}px`,
  }
}

async function showPanel() {
  if (props.disabled) return
  syncView()
  open.value = true
  await nextTick()
  positionPanel()
}

function closePanel({ focus = false } = {}) {
  if (!open.value) return
  open.value = false
  if (focus) nextTick(() => trigger.value?.focus())
}

function commit(value) {
  if (isOutsideBounds(value)) return
  invalid.value = false
  emit('update:modelValue', value)
  emit('change', { target: { value }, value })
  closePanel({ focus: true })
}

function clearValue() {
  invalid.value = props.required
  emit('update:modelValue', '')
  emit('change', { target: { value: '' }, value: '' })
  closePanel({ focus: true })
}

function chooseMonth(month) {
  commit(monthValue(viewYear.value, month))
}

function chooseDay(day) {
  commit(day.value)
}

function changeMonth(offset) {
  const next = new Date(viewYear.value, viewMonth.value + offset, 1)
  viewYear.value = next.getFullYear()
  viewMonth.value = next.getMonth()
}

function changeYear(offset) {
  viewYear.value += offset
}

function onTriggerKeydown(event) {
  if (['Enter', ' ', 'ArrowDown'].includes(event.key)) {
    event.preventDefault()
    if (!open.value) showPanel()
  } else if (event.key === 'Escape') {
    closePanel()
  }
}

function handleDocumentPointer(event) {
  if (root.value?.contains(event.target) || panel.value?.contains(event.target)) return
  closePanel()
}

function handleViewportChange() {
  if (open.value) positionPanel()
}

function validateRequired(event) {
  if (!props.required || props.disabled || props.modelValue) return
  event.preventDefault()
  invalid.value = true
  trigger.value?.focus()
}

watch(() => props.modelValue, () => {
  invalid.value = false
})
watch(() => props.disabled, (disabled) => {
  if (disabled) closePanel()
})

const listenerBinding = createActivationBinding(
  () => {
    containingForm = root.value?.closest('form') || null
    containingForm?.addEventListener('submit', validateRequired, true)
    document.addEventListener('pointerdown', handleDocumentPointer)
    window.addEventListener('resize', handleViewportChange)
    window.addEventListener('scroll', handleViewportChange, true)
  },
  () => {
    containingForm?.removeEventListener('submit', validateRequired, true)
    containingForm = null
    document.removeEventListener('pointerdown', handleDocumentPointer)
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
  <div
    ref="root"
    class="app-date-picker"
    :class="[$attrs.class, { 'is-open': open, 'is-disabled': disabled, 'is-invalid': invalid }]"
    :style="$attrs.style"
  >
    <button
      :id="id"
      ref="trigger"
      type="button"
      class="app-date-picker__trigger"
      :disabled="disabled"
      :aria-label="$attrs['aria-label'] || (mode === 'month' ? '选择月份' : '选择日期')"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-controls="open ? panelId : undefined"
      @click="open ? closePanel() : showPanel()"
      @keydown="onTriggerKeydown"
    >
      <span :class="{ placeholder: !modelValue }">{{ displayValue }}</span>
      <i class="app-date-picker__icon" aria-hidden="true"></i>
    </button>

    <Teleport to="body">
      <section
        v-if="open"
        :id="panelId"
        ref="panel"
        class="app-date-picker__panel"
        :class="`mode-${mode}`"
        :style="panelStyle"
        role="dialog"
        :aria-label="mode === 'month' ? '月份选择器' : '日期选择器'"
        @keydown.esc="closePanel({ focus: true })"
      >
        <header class="app-date-picker__header">
          <button type="button" :aria-label="mode === 'month' ? '上一年' : '上一年'" title="上一年" @click="changeYear(-1)">«</button>
          <button v-if="mode === 'date'" type="button" aria-label="上个月" title="上个月" @click="changeMonth(-1)">‹</button>
          <strong>{{ panelTitle }}</strong>
          <button v-if="mode === 'date'" type="button" aria-label="下个月" title="下个月" @click="changeMonth(1)">›</button>
          <button type="button" aria-label="下一年" title="下一年" @click="changeYear(1)">»</button>
        </header>

        <div v-if="mode === 'month'" class="app-date-picker__months">
          <button
            v-for="(label, month) in months"
            :key="label"
            type="button"
            :disabled="isOutsideBounds(monthValue(viewYear, month))"
            :class="{ selected: modelValue === monthValue(viewYear, month), current: thisMonthValue === monthValue(viewYear, month) }"
            @click="chooseMonth(month)"
          >{{ label }}</button>
        </div>

        <template v-else>
          <div class="app-date-picker__weekdays" aria-hidden="true"><span v-for="day in weekdays" :key="day">{{ day }}</span></div>
          <div class="app-date-picker__days">
            <button
              v-for="day in calendarDays"
              :key="day.value"
              type="button"
              :disabled="day.disabled"
              :class="{ muted: !day.currentMonth, selected: modelValue === day.value, today: day.today }"
              :aria-label="day.value"
              @click="chooseDay(day)"
            >{{ day.day }}</button>
          </div>
        </template>

        <footer class="app-date-picker__footer">
          <button type="button" @click="clearValue">清除</button>
          <button
            type="button"
            :disabled="isOutsideBounds(mode === 'month' ? thisMonthValue : todayValue)"
            @click="commit(mode === 'month' ? thisMonthValue : todayValue)"
          >{{ mode === 'month' ? '本月' : '今天' }}</button>
        </footer>
      </section>
    </Teleport>
  </div>
</template>

<style>
.app-date-picker{position:relative;width:100%;height:43px;min-width:0}.app-date-picker__trigger{width:100%;height:100%;min-height:0;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 12px;color:var(--ink);border:1px solid #bdcbd5;border-radius:3px;background:#fbfcfd;text-align:left}.app-date-picker__trigger>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.app-date-picker__trigger .placeholder{color:#7b8b96}.app-date-picker__icon{position:relative;width:17px;height:16px;flex:0 0 17px;border:1.5px solid currentColor;border-radius:2px}.app-date-picker__icon::before{content:'';position:absolute;top:3px;right:0;left:0;border-top:1.5px solid currentColor}.app-date-picker__icon::after{content:'';position:absolute;top:-3px;left:3px;width:7px;height:4px;border-right:1.5px solid currentColor;border-left:1.5px solid currentColor}.app-date-picker.is-open .app-date-picker__trigger,.app-date-picker__trigger:focus-visible{border-color:var(--blue);outline:0;box-shadow:0 0 0 3px rgba(13,111,158,.1)}.app-date-picker.is-disabled{opacity:.55}.app-date-picker.is-invalid .app-date-picker__trigger{border-color:#c94a43;box-shadow:0 0 0 3px rgba(201,74,67,.1)}.app-date-picker__panel{position:fixed;z-index:1900;overflow:auto;padding:12px;border:1px solid #bdcbd5;border-radius:5px;background:#fff;box-shadow:0 16px 40px rgba(17,49,70,.22);overscroll-behavior:contain}.app-date-picker__header{display:grid;grid-template-columns:34px 34px minmax(0,1fr) 34px 34px;align-items:center;gap:4px;padding-bottom:10px;border-bottom:1px solid #e1e7eb}.app-date-picker__panel.mode-month .app-date-picker__header{grid-template-columns:36px minmax(0,1fr) 36px}.app-date-picker__header strong{text-align:center;color:var(--navy);font-size:14px}.app-date-picker__header button{min-width:0;min-height:32px;padding:0;color:#426174;border:0;background:transparent;font-size:20px}.app-date-picker__header button:hover{background:#edf6f8}.app-date-picker__weekdays,.app-date-picker__days{display:grid;grid-template-columns:repeat(7,1fr);gap:4px}.app-date-picker__weekdays{padding:11px 2px 5px;color:#72838f;font-size:11px;text-align:center}.app-date-picker__days button,.app-date-picker__months button{min-width:0;min-height:36px;padding:0;border:0;border-radius:3px;background:#fff;color:#294050;font-size:13px}.app-date-picker__days button:hover,.app-date-picker__months button:hover{background:#eaf5f7}.app-date-picker__days button.muted{color:#a8b3ba}.app-date-picker__days button.today,.app-date-picker__months button.current{box-shadow:inset 0 0 0 1px #6eabba;color:var(--blue);font-weight:700}.app-date-picker__days button.selected,.app-date-picker__months button.selected{color:#fff;background:var(--blue);box-shadow:none;font-weight:700}.app-date-picker__days button:disabled,.app-date-picker__months button:disabled{color:#c2c9ce;background:#fff;opacity:1;cursor:not-allowed}.app-date-picker__months{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;padding:13px 0 3px}.app-date-picker__months button{min-height:42px}.app-date-picker__footer{display:flex;align-items:center;justify-content:space-between;margin-top:10px;padding-top:10px;border-top:1px solid #e1e7eb}.app-date-picker__footer button{min-height:32px;padding:0 10px;color:var(--blue);border:0;background:transparent;font-size:12px}.app-date-picker__footer button:hover{background:#edf6f8}
</style>
