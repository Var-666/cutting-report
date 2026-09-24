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
  useSlots,
  watch,
} from 'vue'
import { createActivationBinding } from '../utils/activationTimer.js'

defineOptions({ inheritAttrs: false })

const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: undefined },
  disabled: Boolean,
  required: Boolean,
  id: { type: String, default: undefined },
})
const emit = defineEmits(['update:modelValue', 'change'])
const attrs = useAttrs()
const slots = useSlots()
const root = ref(null)
const trigger = ref(null)
const menu = ref(null)
const open = ref(false)
const activeIndex = ref(-1)
const invalid = ref(false)
const menuStyle = ref({})
const menuNaturalSize = ref({ width: 0, height: 0 })

function textContent(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(textContent).join('')
  if (typeof value === 'object') return textContent(value.children)
  return ''
}

function collectOptions(nodes, group = '', output = []) {
  for (const node of nodes || []) {
    if (!node || typeof node !== 'object') continue
    if (node.type === 'option') {
      const label = textContent(node.children).trim()
      output.push({
        key: node.key ?? `${group}-${output.length}`,
        value: node.props?.value ?? label,
        label,
        group,
        disabled: node.props?.disabled === '' || Boolean(node.props?.disabled),
      })
      continue
    }
    const nextGroup = node.type === 'optgroup' ? String(node.props?.label || '') : group
    const children = typeof node.children?.default === 'function'
      ? node.children.default()
      : Array.isArray(node.children) ? node.children : []
    collectOptions(children, nextGroup, output)
  }
  return output
}

const options = computed(() => collectOptions(slots.default?.() || []))
const currentValue = computed(() => props.modelValue !== undefined ? props.modelValue : attrs.value)

function sameValue(left, right) {
  if (left === right) return true
  if (left === null || left === undefined || right === null || right === undefined) return false
  return String(left) === String(right)
}

const selectedIndex = computed(() => options.value.findIndex((item) => sameValue(item.value, currentValue.value)))
const selectedOption = computed(() => options.value[selectedIndex.value] || options.value[0] || null)
const buttonLabel = computed(() => selectedOption.value?.label || '请选择')
const nativeAttrs = computed(() => {
  const { class: className, style, value, ...rest } = attrs
  return rest
})

function enabledIndex(start, step) {
  const length = options.value.length
  if (!length) return -1
  let index = start
  for (let count = 0; count < length; count += 1) {
    index = (index + step + length) % length
    if (!options.value[index]?.disabled) return index
  }
  return -1
}

function positionMenu() {
  if (!open.value || !trigger.value) return
  const rect = trigger.value.getBoundingClientRect()
  const gutter = 6
  const viewportPadding = 10
  const viewportWidth = window.innerWidth - viewportPadding * 2
  const width = Math.min(Math.max(rect.width, menuNaturalSize.value.width), viewportWidth)
  const preferredLeft = rect.left + width > window.innerWidth - viewportPadding
    ? rect.right - width
    : rect.left
  const left = Math.min(Math.max(viewportPadding, preferredLeft), window.innerWidth - width - viewportPadding)
  const availableBelow = Math.max(48, window.innerHeight - rect.bottom - viewportPadding - gutter)
  const availableAbove = Math.max(48, rect.top - viewportPadding - gutter)
  const naturalHeight = Math.min(300, menuNaturalSize.value.height || 300)
  const openAbove = naturalHeight > availableBelow && availableAbove > availableBelow
  const maxHeight = Math.min(300, openAbove ? availableAbove : availableBelow)
  const renderedHeight = Math.min(naturalHeight, maxHeight)
  menuStyle.value = {
    top: `${Math.round(openAbove ? rect.top - gutter - renderedHeight : rect.bottom + gutter)}px`,
    left: `${Math.round(left)}px`,
    width: `${Math.round(width)}px`,
    maxHeight: `${Math.floor(maxHeight)}px`,
    visibility: 'visible',
  }
}

async function showMenu(preferredIndex = selectedIndex.value) {
  if (props.disabled || !options.value.length) return
  menuStyle.value = {
    top: '0px',
    left: '0px',
    width: 'max-content',
    maxWidth: 'calc(100vw - 20px)',
    maxHeight: '300px',
    visibility: 'hidden',
  }
  open.value = true
  activeIndex.value = options.value[preferredIndex]?.disabled
    ? enabledIndex(preferredIndex, 1)
    : preferredIndex >= 0 ? preferredIndex : enabledIndex(-1, 1)
  await nextTick()
  const naturalRect = menu.value?.getBoundingClientRect()
  menuNaturalSize.value = {
    width: Math.ceil(Math.max(naturalRect?.width || 0, menu.value?.scrollWidth || 0)),
    height: Math.ceil(menu.value?.scrollHeight || naturalRect?.height || 0),
  }
  positionMenu()
  menu.value?.querySelector(`[data-option-index="${activeIndex.value}"]`)?.scrollIntoView({ block: 'nearest' })
}

function closeMenu({ focus = false } = {}) {
  if (!open.value) return
  open.value = false
  if (focus) nextTick(() => trigger.value?.focus())
}

function choose(item, index) {
  if (item.disabled) return
  invalid.value = false
  emit('update:modelValue', item.value)
  emit('change', { target: { value: item.value }, value: item.value })
  activeIndex.value = index
  closeMenu({ focus: true })
}

function onTriggerKeydown(event) {
  if (['Enter', ' ', 'ArrowDown', 'ArrowUp'].includes(event.key)) {
    event.preventDefault()
    if (!open.value) showMenu()
    else if (event.key === 'ArrowDown') activeIndex.value = enabledIndex(activeIndex.value, 1)
    else if (event.key === 'ArrowUp') activeIndex.value = enabledIndex(activeIndex.value, -1)
    else {
      const item = options.value[activeIndex.value]
      if (item) choose(item, activeIndex.value)
    }
    return
  }
  if (event.key === 'Escape') closeMenu()
}

function onMenuKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = enabledIndex(activeIndex.value, event.key === 'ArrowDown' ? 1 : -1)
    menu.value?.querySelector(`[data-option-index="${activeIndex.value}"]`)?.scrollIntoView({ block: 'nearest' })
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    const item = options.value[activeIndex.value]
    if (item) choose(item, activeIndex.value)
  } else if (event.key === 'Escape' || event.key === 'Tab') {
    closeMenu({ focus: event.key === 'Escape' })
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    activeIndex.value = event.key === 'Home'
      ? enabledIndex(-1, 1)
      : enabledIndex(0, -1)
  }
}

function handleDocumentPointer(event) {
  if (root.value?.contains(event.target) || menu.value?.contains(event.target)) return
  closeMenu()
}

function handleInvalid(event) {
  event.preventDefault()
  invalid.value = true
  trigger.value?.focus()
}

function handleViewportChange() {
  if (open.value) positionMenu()
}

watch(() => props.disabled, (disabled) => {
  if (disabled) closeMenu()
})
watch(currentValue, () => {
  invalid.value = false
})

const listenerBinding = createActivationBinding(
  () => {
    document.addEventListener('pointerdown', handleDocumentPointer)
    window.addEventListener('resize', handleViewportChange)
    window.addEventListener('scroll', handleViewportChange, true)
  },
  () => {
    document.removeEventListener('pointerdown', handleDocumentPointer)
    window.removeEventListener('resize', handleViewportChange)
    window.removeEventListener('scroll', handleViewportChange, true)
  },
)

function pause() {
  closeMenu()
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
    class="app-select"
    :class="[$attrs.class, { 'is-open': open, 'is-disabled': disabled, 'is-invalid': invalid }]"
    :style="$attrs.style"
  >
    <button
      :id="id"
      ref="trigger"
      class="app-select__trigger"
      type="button"
      :disabled="disabled"
      :aria-label="$attrs['aria-label']"
      aria-haspopup="listbox"
      :aria-expanded="open"
      @click="open ? closeMenu() : showMenu()"
      @keydown="onTriggerKeydown"
    >
      <span :title="buttonLabel">{{ buttonLabel }}</span>
      <i aria-hidden="true"></i>
    </button>
    <select
      v-bind="nativeAttrs"
      class="app-select__native"
      :value="currentValue"
      :disabled="disabled"
      :required="required"
      tabindex="-1"
      aria-hidden="true"
      @invalid="handleInvalid"
    ><slot /></select>
    <Teleport to="body">
      <div
        v-if="open"
        ref="menu"
        class="app-select__menu"
        :style="menuStyle"
        role="listbox"
        :aria-label="$attrs['aria-label']"
        tabindex="-1"
        @keydown="onMenuKeydown"
      >
        <template v-for="(item, index) in options" :key="item.key">
          <div v-if="item.group && item.group !== options[index - 1]?.group" class="app-select__group">{{ item.group }}</div>
          <button
            type="button"
            role="option"
            :data-option-index="index"
            :aria-selected="sameValue(item.value, currentValue)"
            :class="{ active: index === activeIndex, selected: sameValue(item.value, currentValue) }"
            :disabled="item.disabled"
            :title="item.label"
            @pointerenter="activeIndex = index"
            @click="choose(item, index)"
          >
            <span class="app-select__check" aria-hidden="true">{{ sameValue(item.value, currentValue) ? '✓' : '' }}</span>
            <span>{{ item.label }}</span>
          </button>
        </template>
      </div>
    </Teleport>
  </div>
</template>

<style>
.app-select{position:relative;width:100%;height:43px;min-width:0}.app-select__trigger{width:100%;height:100%;min-height:0;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 12px;color:var(--ink);border:1px solid #bdcbd5;border-radius:3px;background:#fbfcfd;text-align:left}.app-select__trigger>span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.app-select__trigger i{width:8px;height:8px;flex:0 0 8px;margin:-4px 2px 0 0;border-right:1.5px solid currentColor;border-bottom:1.5px solid currentColor;transform:rotate(45deg);transition:transform .15s ease}.app-select.is-open .app-select__trigger,.app-select__trigger:focus-visible{border-color:var(--blue);outline:0;box-shadow:0 0 0 3px rgba(13,111,158,.1)}.app-select.is-open .app-select__trigger i{margin-top:4px;transform:rotate(225deg)}.app-select.is-disabled{opacity:.55}.app-select__native{position:absolute!important;width:1px!important;height:1px!important;inset:auto!important;overflow:hidden!important;margin:0!important;padding:0!important;border:0!important;opacity:0!important;pointer-events:none!important}.app-select__menu{position:fixed;z-index:2000;overflow-y:auto;padding:5px;border:1px solid #bdcbd5;border-radius:5px;background:#fff;box-shadow:0 14px 36px rgba(17,49,70,.2);overscroll-behavior:contain}.app-select__menu>button{width:100%;min-height:36px;display:grid;grid-template-columns:20px minmax(0,1fr);align-items:center;gap:5px;padding:6px 9px;color:#263b4a;border:0;border-radius:3px;background:#fff;text-align:left;font-size:13px}.app-select__menu>button>span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.app-select__menu>button:hover,.app-select__menu>button.active{background:#edf6f8}.app-select__menu>button.selected{color:var(--navy);font-weight:700}.app-select__menu>button:disabled{color:#8a98a2;background:#fff;opacity:1}.app-select__check{color:var(--blue);font-size:15px;text-align:center}.app-select__group{padding:9px 9px 5px;color:#71818d;font-size:11px;font-weight:700;letter-spacing:.4px}.app-select.is-invalid .app-select__trigger{border-color:#c94a43;box-shadow:0 0 0 3px rgba(201,74,67,.1)}
</style>
