import type { Ref } from 'vue'
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useConfirm } from './useConfirm.ts'

export function useUnsavedChanges(
  dirty: Readonly<Ref<boolean>>,
  message = '当前页面有未保存的修改，离开将丢弃这些内容。',
  busy?: Readonly<Ref<boolean>>,
  { guardRoute = true } = {},
) {
  const confirm = useConfirm()
  let active = true
  let generation = 0
  let pending: Promise<boolean> | undefined
  let navigation: Promise<boolean> | undefined

  function canDiscard(content = message, changed = dirty.value): boolean | Promise<boolean> {
    if (!active || busy?.value || pending) return false
    if (!changed) return true
    const requestGeneration = generation
    pending = confirm({
      title: '放弃未保存的修改？', content,
      positiveText: '放弃修改', negativeText: '继续编辑', closable: false,
    }).then(accepted => accepted && active && requestGeneration === generation && !busy?.value)
      .finally(() => { pending = undefined })
    return pending
  }

  function beforeUnload(event: BeforeUnloadEvent) {
    event.preventDefault()
    event.returnValue = ''
  }
  const needsProtection = computed(() => dirty.value || Boolean(busy?.value))
  function syncBeforeUnload() {
    if (!guardRoute || typeof window === 'undefined') return
    window.removeEventListener('beforeunload', beforeUnload)
    if (active && needsProtection.value) window.addEventListener('beforeunload', beforeUnload)
  }
  function deactivate() {
    active = false
    generation += 1
    syncBeforeUnload()
  }
  onMounted(syncBeforeUnload)
  onActivated(() => { active = true; syncBeforeUnload() })
  onDeactivated(deactivate)
  onBeforeUnmount(deactivate)
  watch(needsProtection, syncBeforeUnload, { flush: 'sync' })

  if (guardRoute) onBeforeRouteLeave(() => {
    if (!navigation) {
      const decision = canDiscard()
      if (typeof decision === 'boolean') return decision
      navigation = decision.finally(() => { navigation = undefined })
    }
    return navigation
  })

  return canDiscard
}
