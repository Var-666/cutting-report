import type { RouteRecordName, RouteRecordRaw } from 'vue-router'
export interface AdminMenu { key: RouteRecordName; label: string; icon?: string; routeName?: RouteRecordName; children?: AdminMenu[] }
export function navigationSection(path: string) {
  if (path === '/admin/home') return 'home'
  if (path === '/admin/automation') return 'automation'
  if (path === '/admin/pdm-statistics') return 'pdm'
  if (path === '/admin/operations' || path.startsWith('/admin/operations/')) return 'operations'
  if (path === '/admin/settings' || path.startsWith('/admin/settings/')) return 'settings'
  return 'line'
}
export function adminMenus(routes: readonly RouteRecordRaw[], parent = '/admin', line = 'cutting'): AdminMenu[] {
  return routes.filter((route) => !route.redirect && (!route.meta?.line || route.meta.line === line)).flatMap((route) => {
    const path = `${parent}/${route.path}`.replace(/\/$/, '')
    const children = route.children ? adminMenus(route.children, path, line) : []
    if (children.length && !route.meta?.title) return children
    if (children.length) {
      const indexRoute = route.children?.find((child) => child.path === '' && child.name && !child.redirect)
      return [{
        key: path, label: route.meta?.title || '', icon: route.meta?.icon,
        routeName: indexRoute?.name,
        children: children.filter((child) => child.key !== indexRoute?.name),
      }]
    }
    return route.name && route.meta?.title
      ? [{ key: route.name, label: route.meta.title || '', icon: route.meta.icon }]
      : []
  })
}
