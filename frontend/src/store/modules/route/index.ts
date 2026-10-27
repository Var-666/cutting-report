import type { RouteRecordName } from 'vue-router'
import type { AdminMenu } from '../../../router/menus'
import { computed, h } from 'vue'
import { defineStore } from 'pinia'
import router from '../../../router'
import { adminMenus } from '../../../router/menus.ts'
import AppIcon from '../../../components/AppIcon.vue'
import { deviceSelection } from '../../../state/devices.ts'
import { selectedLineType } from '../../../utils/productionLines.ts'

export const useRouteStore = defineStore('theme-route-adapter', () => {
  const menus = computed(() => {
    function convert(items: AdminMenu[]): App.Global.Menu[] {
      return items.map(item => ({
        ...item,
        key: String(item.key),
        routeKey: String(item.key),
        routePath: router.resolve(item.routeName ? { name: item.routeName } : item.children ? String(item.key) : { name: item.key }).path,
        onClick: item.routeName ? () => router.push({ name: item.routeName }) : undefined,
        icon: () => h(AppIcon, { name: item.icon || '', size: 20, tone: 'current' }),
        children: item.children ? convert(item.children) : undefined,
      }))
    }
    return convert(adminMenus(router.options.routes.find(item => item.path === '/admin')?.children || [], '/admin', selectedLineType(deviceSelection.items, deviceSelection.id)))
  })
  function getSelectedMenuKeyPath(key: RouteRecordName, items = menus.value, parents: string[] = []): string[] {
    for (const item of items) {
      const path = [...parents, item.key]
      if (item.key === key || item.routeName === key) return path
      const found = item.children && getSelectedMenuKeyPath(key, item.children, path)
      if (found?.length) return found
    }
    return []
  }
  function findMenu(key: RouteRecordName, items = menus.value): App.Global.Menu | undefined {
    for (const item of items) {
      if (item.key === key || item.routeName === key) return item
      const child = item.children && findMenu(key, item.children)
      if (child) return child
    }
  }
  const searchMenus = computed(() => {
    const flat = (items: App.Global.Menu[]): App.Global.Menu[] => items.flatMap(item => item.children ? [ ...(item.routeName ? [item] : []), ...flat(item.children)] : [item])
    return flat(menus.value)
  })
  return { menus, searchMenus, getSelectedMenuKeyPath, findMenu }
})
