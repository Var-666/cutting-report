export function adminMenus(routes, parent = '/admin') {
  return routes.filter((route) => !route.redirect).flatMap((route) => {
    const path = `${parent}/${route.path}`.replace(/\/$/, '')
    const children = route.children ? adminMenus(route.children, path) : []
    if (children.length === 1 && !route.meta?.title) return children
    if (children.length) {
      const indexRoute = route.children.find((child) => child.path === '' && child.name && !child.redirect)
      return [{
        key: path, label: route.meta.title, icon: route.meta.icon,
        routeName: indexRoute?.name,
        children: children.filter((child) => child.key !== indexRoute?.name),
      }]
    }
    return route.name && route.meta?.title
      ? [{ key: route.name, label: route.meta.title, icon: route.meta.icon }]
      : []
  })
}
