<script setup>
import heroImage from '../assets/home-laser-cutting.webp'
import AppIcon from './AppIcon.vue'

defineProps({
  eyebrow: { type: String, required: true },
  title: { type: String, required: true },
  description: { type: String, required: true },
  icon: { type: String, default: 'settings' },
  statusLabel: { type: String, default: '系统状态' },
  statusValue: { type: String, required: true },
  statusNote: { type: String, default: '' },
  tone: { type: String, default: 'blue' },
})
</script>

<template>
  <section class="management-hero" :style="{ '--management-hero-image': `url(${heroImage})` }">
    <div class="management-hero__copy">
      <span>{{ eyebrow }}</span>
      <h1>{{ title }}</h1>
      <p>{{ description }}</p>
    </div>
    <aside class="management-hero__status" :class="`tone-${tone}`">
      <span class="management-hero__icon"><AppIcon :name="icon" :size="23" /></span>
      <div>
        <small>{{ statusLabel }}</small>
        <strong>{{ statusValue }}</strong>
        <span v-if="statusNote">{{ statusNote }}</span>
      </div>
    </aside>
  </section>
</template>

<style scoped>
.management-hero{position:relative;min-height:176px;display:grid;grid-template-columns:minmax(420px,1fr) 270px;align-items:center;gap:32px;overflow:hidden;padding:26px 30px;border:1px solid rgba(255,255,255,.78);border-radius:14px;background:linear-gradient(90deg,rgba(247,252,254,.98) 0%,rgba(237,247,251,.93) 46%,rgba(9,55,83,.18) 76%),var(--management-hero-image) center 47%/cover no-repeat;box-shadow:0 12px 30px rgba(22,62,89,.07)}
.management-hero::after{content:"";position:absolute;right:0;bottom:0;width:46%;height:3px;background:linear-gradient(90deg,transparent,#237fb7 48%,#16a39d)}
.management-hero__copy,.management-hero__status{position:relative;z-index:1}.management-hero__copy>span{display:block;color:#237fb7;font-size:10px;font-weight:800;letter-spacing:1.45px}.management-hero h1{margin:6px 0;color:#153f5d;font-size:38px;line-height:1.08;letter-spacing:1px}.management-hero__copy p{max-width:650px;margin:0;color:#496779;font-size:12px;line-height:1.75}
.management-hero__status{display:flex;align-items:center;gap:12px;justify-self:end;width:250px;padding:15px 16px;border:1px solid rgba(69,128,154,.2);border-radius:11px;background:rgba(255,255,255,.91);box-shadow:0 12px 28px rgba(20,62,88,.13)}.management-hero__icon{width:40px;height:40px;display:grid;place-items:center;flex:0 0 auto;border-radius:10px;background:#eaf5fb}.management-hero__status div{min-width:0}.management-hero__status small,.management-hero__status strong,.management-hero__status div>span{display:block}.management-hero__status small{color:#708898;font-size:9px}.management-hero__status strong{margin-top:2px;overflow:hidden;color:#176f9e;font-size:15px;text-overflow:ellipsis;white-space:nowrap}.management-hero__status div>span{margin-top:3px;color:#60798a;font-size:9px;line-height:1.45}.management-hero__status.tone-teal .management-hero__icon{background:#e8f7f5}.management-hero__status.tone-teal strong{color:#14867f}.management-hero__status.tone-green .management-hero__icon{background:#eaf7f1}.management-hero__status.tone-green strong{color:#23825e}.management-hero__status.tone-violet .management-hero__icon{background:#efedff}.management-hero__status.tone-violet strong{color:#6557c7}.management-hero__status.tone-amber .management-hero__icon{background:#fff5e8}.management-hero__status.tone-amber strong{color:#b97620}
@media(max-width:900px){.management-hero{grid-template-columns:1fr;min-height:0}.management-hero__status{justify-self:start;width:min(100%,360px)}}
@media(max-width:560px){.management-hero{padding:22px 18px}.management-hero h1{font-size:31px}.management-hero__status{width:100%}}
</style>
