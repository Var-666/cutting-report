import type { Ref } from 'vue'
import { nextTick, onBeforeUnmount, watch } from 'vue'

const focusableSelector = [
  'button:not([disabled])',
  '[href]',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function useDialogFocus(open: Readonly<Ref<boolean>>, dialogElement: Ref<HTMLElement | null>, close: () => void) {
  let previousFocus: Element | null = null

  function handleKeydown(event: KeyboardEvent) {
    if (!open.value || !dialogElement.value) return
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
      return
    }
    if (event.key !== 'Tab') return
    const focusable = [...dialogElement.value.querySelectorAll<HTMLElement>(focusableSelector)]
      .filter((element) => !element.hidden && element.getAttribute('aria-hidden') !== 'true')
    if (!focusable.length) {
      event.preventDefault()
      dialogElement.value.focus()
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const stop = watch(open, async (visible) => {
    if (visible) {
      previousFocus = document.activeElement
      document.addEventListener('keydown', handleKeydown)
      await nextTick()
      const preferred = dialogElement.value?.querySelector<HTMLElement>('[data-dialog-initial-focus]')
      const first = dialogElement.value?.querySelector<HTMLElement>(focusableSelector)
      ;(preferred || first || dialogElement.value)?.focus()
    } else {
      document.removeEventListener('keydown', handleKeydown)
      previousFocus instanceof HTMLElement && previousFocus.focus()
      previousFocus = null
    }
  })

  onBeforeUnmount(() => {
    stop()
    document.removeEventListener('keydown', handleKeydown)
  })
}
