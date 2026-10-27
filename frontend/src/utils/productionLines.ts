import type { ProductionLine } from '../types/devices'

const lineTypes: Record<string, { name: string; home: string }> = {
  cutting: { name: '切割', home: '/admin/home' },
  welding: { name: '焊接', home: '/admin/welding/home' },
}

export function productionGroups(items: ProductionLine[]) {
  const groups = new Map<string, { id: string; name: string; items: ProductionLine[] }>()
  for (const item of items) {
    const type = item.type || 'cutting'
    if (!groups.has(type)) groups.set(type, { id: type, name: item.type_name || lineTypes[type]?.name || type, items: [] })
    groups.get(type)!.items.push(item)
  }
  return [...groups.values()]
}

export function selectedLineType(items: ProductionLine[], id: string) {
  return items.find(item => item.id === id)?.type || 'cutting'
}

export function lineHome(items: ProductionLine[], id: string) {
  const line = items.find(item => item.id === id)
  return line?.home_path || lineTypes[selectedLineType(items, id)]?.home || '/admin/home'
}
