import type { LocationQueryRaw, RouteRecordName } from 'vue-router'
import { onBeforeUnmount } from 'vue'
import { registerDeviceSwitchQuery } from '../api/deviceContext.ts'

// Reports already serialize their filters into route queries. Reuse that state
// when switching devices, including the date of a default latest-day view.
export function useDeviceSwitchQuery(routeName: RouteRecordName, readQuery: () => LocationQueryRaw) {
  const unregister = registerDeviceSwitchQuery(routeName, readQuery)
  onBeforeUnmount(unregister)
}
