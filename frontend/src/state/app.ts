import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  const reducedMotion = ref(false)
  const isMobile = ref(false)
  const siderCollapse = ref(false)
  const themeDrawerVisible = ref(false)
  const mixSiderFixed = ref(true)

  function setMobile(value: boolean) {
    if (isMobile.value === value) return
    isMobile.value = value
    siderCollapse.value = value
  }

  return {
    isMobile, siderCollapse, setMobile, reducedMotion, themeDrawerVisible, mixSiderFixed,
    openThemeDrawer: () => { themeDrawerVisible.value = true },
    toggleSiderCollapse: () => { siderCollapse.value = !siderCollapse.value },
    toggleMixSiderFixed: () => { mixSiderFixed.value = !mixSiderFixed.value },
  }
})
