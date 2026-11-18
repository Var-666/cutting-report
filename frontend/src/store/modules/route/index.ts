import type { RouteRecordName } from 'vue-router'
import type { AdminMenu } from '../../../router/menus'
import { computed, h } from 'vue'
import { defineStore } from 'pinia'
import router from '../../../router'
import { adminMenus, navigationSection } from '../../../router/menus.ts'
import AppIcon from '../../../components/AppIcon.vue'
import { deviceSelection } from '../../../state/devices.ts'
import { lineHome, productionGroups, productionLineIcon, selectedLineType } from '../../../utils/productionLines.ts'

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
  const section = computed(() => navigationSection(router.currentRoute.value.path))
  const sharedKeys = new Set(['admin-home', 'automation', 'pdm-statistics', '/admin/settings', '/admin/operations'])
  const sharedMenus = computed(() => menus.value.filter(menu => sharedKeys.has(menu.key)))
  const lineMenus = computed(() => menus.value.filter(menu => !sharedKeys.has(menu.key)))
  const settingsMenu = computed(() => sharedMenus.value.find(menu => menu.key === '/admin/settings'))
  const operationsMenu = computed(() => sharedMenus.value.find(menu => menu.key === '/admin/operations'))
  const secondaryMenus = computed(() => {
    if (section.value === 'settings') return settingsMenu.value?.children || []
    if (section.value === 'operations') return operationsMenu.value?.children || []
    return section.value === 'line' ? lineMenus.value : []
  })
  const activeLine = computed(() => deviceSelection.items.find(item => item.id === deviceSelection.id))
  const primaryKey = computed(() => {
    if (section.value === 'line') return `line:${deviceSelection.id}`
    if (section.value === 'settings') return '/admin/settings'
    if (section.value === 'operations') return '/admin/operations'
    return String(router.currentRoute.value.name)
  })
  function lineGroups(expandCurrent: boolean): App.Global.Menu[] {
    return productionGroups(deviceSelection.items).map(group => ({
      key: `type:${group.id}`, routeKey: `type:${group.id}`, routePath: '', label: group.name,
      icon: () => h(AppIcon, { name: productionLineIcon(group.id), size: 20, tone: 'current' }),
      children: group.items.map(line => ({
        key: `line:${line.id}`, routeKey: `line:${line.id}`, routePath: lineHome(deviceSelection.items, line.id), label: line.name,
        icon: () => h(AppIcon, { name: productionLineIcon(group.id), size: 20, tone: 'current' }),
        ...(expandCurrent && section.value === 'line' && line.id === deviceSelection.id ? {
          children: lineMenus.value,
          onClick: () => router.push(lineHome(deviceSelection.items, line.id)),
        } : {}),
      })),
    }))
  }
  function primaryNavigation(expandCurrent: boolean) {
    return [
      ...sharedMenus.value.filter(menu => menu.key === 'admin-home' || menu.key === 'automation'),
      ...lineGroups(expandCurrent),
      ...sharedMenus.value.filter(menu => menu.key === 'pdm-statistics'),
      ...(operationsMenu.value ? [expandCurrent ? operationsMenu.value : { ...operationsMenu.value, children: undefined }] : []),
    ]
  }
  const primaryMenus = computed(() => primaryNavigation(false))
  const navigationMenus = computed(() => [...primaryNavigation(true), ...(settingsMenu.value ? [settingsMenu.value] : [])])
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
  return { menus, searchMenus, getSelectedMenuKeyPath, findMenu, section, primaryKey, primaryMenus, navigationMenus, secondaryMenus, settingsMenu, activeLine }
})
