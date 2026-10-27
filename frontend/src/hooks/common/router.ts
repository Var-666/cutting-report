import type { RouteRecordName } from 'vue-router'
import { useRouter } from 'vue-router'
import { useRouteStore } from '../../store/modules/route'
export function useRouterPush() {
  const router = useRouter()
  const routes = useRouteStore()
  return { routerPushByKeyWithMetaQuery(key: RouteRecordName) {
    const menu = routes.findMenu(key)
    if (!menu) return
    return router.push(menu.routeName ? { name: menu.routeName } : menu.children ? menu.routePath : { name: menu.key })
  } }
}
