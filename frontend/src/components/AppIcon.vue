<script setup>
import { computed, useId } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: 18 },
  strokeWidth: { type: [Number, String], default: 1.8 },
  tone: { type: String, default: '' },
})

const palettes = {
  muted: ['#56768c', '#829bac'],
  blue: ['#2187da', '#62c8ef'],
  teal: ['#079a9b', '#54d8bd'],
  green: ['#159d69', '#62d49a'],
  amber: ['#e18424', '#ffc45c'],
  red: ['#df4f5d', '#ff8f7d'],
  violet: ['#6458d6', '#a38cf4'],
  sky: ['#2479ce', '#55b9ed'],
  light: ['#ffffff', '#bff5ff'],
}

const iconTones = {
  home: 'sky', report: 'blue', bell: 'amber', send: 'violet', settings: 'teal',
  calendar: 'blue', tasks: 'sky', ruler: 'teal', clock: 'violet', factory: 'green',
  zap: 'amber', wind: 'sky', trend: 'teal', activity: 'violet', file: 'teal',
  grid: 'violet', sparkles: 'violet', briefcase: 'blue', chart: 'sky', check: 'green',
  alert: 'red', 'arrow-up': 'green', 'arrow-down': 'red', lightbulb: 'amber',
  search: 'sky', download: 'teal',
  'chevron-right': 'blue', database: 'teal', user: 'sky', logout: 'red',
}

const palette = computed(() => palettes[props.tone || iconTones[props.name]] || palettes.blue)
const gradientId = `app-icon-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
const gradientPaint = computed(() => `url(#${gradientId})`)
const iconStyle = computed(() => ({
  '--app-icon-main': palette.value[0],
  '--app-icon-accent': palette.value[1],
}))
</script>

<template>
  <svg
    class="app-icon"
    viewBox="0 0 24 24"
    fill="none"
    :width="size"
    :height="size"
    :style="iconStyle"
    :stroke-width="strokeWidth"
    :stroke="gradientPaint"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <defs>
      <linearGradient :id="gradientId" x1="3" y1="3" x2="21" y2="21" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="var(--app-icon-accent)"/>
        <stop offset=".52" stop-color="var(--app-icon-main)"/>
        <stop offset="1" stop-color="var(--app-icon-main)"/>
      </linearGradient>
    </defs>
    <template v-if="name === 'home'"><path d="m3 10 9-7 9 7"/><path d="M5 9v11h14V9M9 20v-7h6v7"/></template>
    <template v-else-if="name === 'report'"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></template>
    <template v-else-if="name === 'bell'"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></template>
    <template v-else-if="name === 'send'"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></template>
    <template v-else-if="name === 'settings'"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></template>
    <template v-else-if="name === 'calendar'"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/></template>
    <template v-else-if="name === 'tasks'"><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3.5h6V6H9zM8 11h.01M11 11h5M8 15h.01M11 15h5"/></template>
    <template v-else-if="name === 'ruler'"><path d="M3 8v8h18V8Z"/><path d="M7 8v4M11 8v2M15 8v4M19 8v2"/></template>
    <template v-else-if="name === 'clock'"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></template>
    <template v-else-if="name === 'factory'"><path d="M3 21V9l6 3V8l6 4V5h6v16Z"/><path d="M7 17h2M12 17h2M17 17h2"/></template>
    <template v-else-if="name === 'zap'"><path d="m13 2-9 12h8l-1 8 9-12h-8Z"/></template>
    <template v-else-if="name === 'wind'"><path d="M3 8h10a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h6"/></template>
    <template v-else-if="name === 'trend'"><path d="M3 20V10M3 20h18"/><path d="m6 15 4-4 3 3 6-7"/><path d="M15 7h4v4"/></template>
    <template v-else-if="name === 'activity'"><path d="M3 12h4l2-6 4 12 2-6h6"/></template>
    <template v-else-if="name === 'file'"><path d="M6 2h8l4 4v16H6Z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></template>
    <template v-else-if="name === 'grid'"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></template>
    <template v-else-if="name === 'sparkles'"><path d="m12 3 1.2 3.8L17 8l-3.8 1.2L12 13l-1.2-3.8L7 8l3.8-1.2ZM5 15l.8 2.2L8 18l-2.2.8L5 21l-.8-2.2L2 18l2.2-.8ZM19 13l.7 1.8 1.8.7-1.8.7L19 18l-.7-1.8-1.8-.7 1.8-.7Z"/></template>
    <template v-else-if="name === 'briefcase'"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M9 7V4h6v3M3 12h18M10 12v2h4v-2"/></template>
    <template v-else-if="name === 'chart'"><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></template>
    <template v-else-if="name === 'check'"><circle cx="12" cy="12" r="9"/><path d="m8 12 2.5 2.5L16 9"/></template>
    <template v-else-if="name === 'alert'"><path d="M12 3 2.7 20h18.6Z"/><path d="M12 9v4M12 17h.01"/></template>
    <template v-else-if="name === 'arrow-up'"><path d="m6 15 6-6 6 6"/></template>
    <template v-else-if="name === 'arrow-down'"><path d="m6 9 6 6 6-6"/></template>
    <template v-else-if="name === 'lightbulb'"><path d="M9 18h6M10 22h4M8.5 15.5a7 7 0 1 1 7 0c-.8.7-1.2 1.4-1.3 2.5h-4.4c-.1-1.1-.5-1.8-1.3-2.5Z"/></template>
    <template v-else-if="name === 'search'"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></template>
    <template v-else-if="name === 'download'"><path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/></template>
    <template v-else-if="name === 'chevron-right'"><path d="m9 18 6-6-6-6"/></template>
    <template v-else-if="name === 'database'"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/></template>
    <template v-else-if="name === 'user'"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></template>
    <template v-else-if="name === 'logout'"><path d="M10 5H5v14h5M14 8l4 4-4 4M9 12h9"/></template>
    <template v-else><circle cx="12" cy="12" r="9"/></template>
  </svg>
</template>

<style scoped>
.app-icon{display:block;overflow:visible;flex:0 0 auto;filter:drop-shadow(0 1px 1px rgba(15,56,80,.1))}
</style>
