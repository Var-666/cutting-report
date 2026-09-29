<script setup>
import { RouterLink } from 'vue-router'
import AppIcon from './AppIcon.vue'

defineProps({
  headingId: { type: String, default: '' },
  title: { type: String, required: true },
  subtitle: { type: String, required: true },
  icon: { type: String, required: true },
  to: { type: String, default: '' },
  action: { type: String, default: '' },
  tone: { type: String, default: 'teal' },
})
</script>

<template>
  <header class="home-section-head" :class="`home-section-head--${tone}`">
    <div class="home-section-head__title"><span class="home-section-head__icon"><AppIcon :name="icon" :size="18" /></span><h2 :id="headingId">{{ title }}<small>{{ subtitle }}</small></h2></div>
    <RouterLink v-if="to" :to="to">{{ action }} <AppIcon name="chevron-right" :size="13" /></RouterLink>
  </header>
</template>

<style scoped>
.home-section-head{--heading-color:#1687a7;min-height:52px;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 16px;border-bottom:1px solid #e6edf2}.home-section-head--blue{--heading-color:#3284d6}.home-section-head--amber{--heading-color:#d99020}.home-section-head--purple{--heading-color:#7668e5}
.home-section-head__title{min-width:0;display:flex;align-items:center;gap:9px}.home-section-head__icon{width:28px;height:28px;display:grid;place-items:center;flex:0 0 auto;color:var(--heading-color);border-radius:7px;background:color-mix(in srgb,var(--heading-color) 10%,white)}.home-section-head h2{min-width:0;display:flex;align-items:baseline;gap:9px;overflow:hidden;margin:0;color:#16324a;font-size:16px;font-weight:650;white-space:nowrap}.home-section-head small{overflow:hidden;color:#74889a;font-size:10px;font-weight:500;letter-spacing:.06em;text-overflow:ellipsis}.home-section-head a{min-height:34px;display:inline-flex;align-items:center;gap:3px;flex:0 0 auto;padding:0 4px;color:#0f5d7a;border-radius:5px;font-size:12px;font-weight:600;text-decoration:none}.home-section-head a:hover{color:#1687a7;text-decoration:underline}.home-section-head a:focus-visible{outline:2px solid #3284d6;outline-offset:2px}
@media(max-width:490px){.home-section-head small{display:none}}
</style>
