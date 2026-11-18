import type { RouteRecordName } from 'vue-router'
import { useRouter } from 'vue-router'
import { useRouteStore } from '../../store/modules/route'
import { useDeviceSelection } from '../../composables/useDeviceSelection.ts'
export function useRouterPush() {
  const router = useRouter()
  const routes = useRouteStore()
  const { enterLine } = useDeviceSelection()
  return { routerPushByKeyWithMetaQuery(key: RouteRecordName) {
    if (typeof key === 'string' && key.startsWith('line:')) return enterLine(key.slice(5))
    const menu = routes.findMenu(key)
    if (!menu) return
    return router.push(menu.routeName ? { name: menu.routeName } : menu.children ? menu.routePath : { name: menu.key })
  } }
}
