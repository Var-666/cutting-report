function textContent(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return ''
  if (typeof value === 'string' || typeof value === 'number') return String(value)
  if (Array.isArray(value)) return value.map(textContent).join('')
  return typeof value === 'object' ? textContent(value.children) : ''
}

// Keep existing option slots and value types while Naive UI owns the dropdown.
export function selectOptions(nodes, group = '', output = []) {
  for (const node of nodes) {
    if (!node || typeof node !== 'object') continue
    if (node.type === 'option') {
      const label = textContent(node.children).trim()
      output.push({
        value: output.length,
        sourceValue: node.props?.value ?? label,
        label: group ? `${group} · ${label}` : label,
        disabled: node.props?.disabled === '' || Boolean(node.props?.disabled),
      })
    } else {
      const children = typeof node.children?.default === 'function' ? node.children.default() : node.children
      if (Array.isArray(children)) selectOptions(children, node.type === 'optgroup' ? node.props?.label || '' : group, output)
    }
  }
  return output
}
