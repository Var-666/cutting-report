import { vi } from 'vitest'

export function stubTableLayout() {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const getStyle = globalThis.getComputedStyle
  // jsdom has no cell layout. Skip its CSS cascade for measurements unrelated to these scenarios.
  vi.spyOn(globalThis, 'getComputedStyle').mockImplementation((element, pseudo) => element.matches('th, td')
    ? { paddingLeft: '0', paddingRight: '0' }
    : getStyle(element, pseudo))
}
