import { onBeforeUnmount } from 'vue'
import { registerDeviceSwitchQuery } from '../api/deviceContext.js'

// Reports already serialize their filters into route queries. Reuse that state
// when switching devices, including the date of a default latest-day view.
export function useDeviceSwitchQuery(routeName, readQuery) {
  const unregister = registerDeviceSwitchQuery(routeName, readQuery)
  onBeforeUnmount(unregister)
}
