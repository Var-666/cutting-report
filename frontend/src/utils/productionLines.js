export function productionGroups(items) {
  const groups = new Map()
  for (const item of items) {
    const type = item.type || 'cutting'
    if (!groups.has(type)) groups.set(type, { id: type, name: item.type_name || (type === 'cutting' ? '切割' : '焊接'), items: [] })
    groups.get(type).items.push(item)
  }
  return [...groups.values()]
}

export function selectedLineType(items, id) {
  return items.find(item => item.id === id)?.type || 'cutting'
}
