import type { PiniaPluginContext } from 'pinia'
import { klona as jsonClone } from 'klona/json'

// Soybean's setup-store reset plugin, scoped to the migrated theme store.
export function resetSetupStore({ store }: PiniaPluginContext) {
  if (store.$id !== 'theme-store') return
  const initial = jsonClone(store.$state)
  store.$reset = () => store.$patch(jsonClone(initial))
}
